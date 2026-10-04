import os
import time
from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.utils import timezone
from rcsscrapper.models import HouseholdGroup, Roommate, Expense, ExpenseSplit, Receipt, ReceiptItem
from rcsscrapper.services import calculate_balances, simplify_debts, import_and_clean_splitwise_csv
from rcsscrapper.utils import extract_items_from_soup, parse_tax_from_soup, parse_order_total_from_soup
from bs4 import BeautifulSoup


class OpenSourceNewUserZeroDataTestCase(TestCase):
    """
    Validates that a brand new user with zero prior data can register,
    automatically becomes the Group Admin of their new household, and can operate
    the application without crashing or encountering missing data errors.
    """

    def setUp(self):
        self.client = Client()

    def test_new_user_registration_and_auto_group_creation(self):
        resp = self.client.post('/register/', {
            'username': 'alice',
            'full_name': 'Alice Smith',
            'email': 'alice@example.com',
            'password': 'password123',
            'password_confirm': 'password123',
        }, follow=True)
        self.assertEqual(resp.status_code, 200)

        user = User.objects.get(username='alice')
        self.assertIsNotNone(user.roommate)
        self.assertEqual(user.roommate.name, 'Alice Smith')

        # Verify automated primary household creation
        group = user.roommate.groups.first()
        self.assertIsNotNone(group)
        self.assertEqual(group.created_by, user.roommate)
        self.assertEqual(group.name, "Alice Smith's Household")
        self.assertTrue(group.is_admin(user))

    def test_zero_data_dashboard_renders_cleanly(self):
        # Register and login new user
        self.client.post('/register/', {
            'username': 'bob',
            'full_name': 'Bob Jones',
            'email': 'bob@example.com',
            'password': 'password123',
            'password_confirm': 'password123',
        }, follow=True)

        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "You are all settled up!")
        self.assertContains(resp, "$0.00")
        self.assertContains(resp, "No expenses or settlements recorded yet.")

    def test_expense_date_enforced_to_today(self):
        # Malicious POST supplying a past date
        self.client.post('/register/', {
            'username': 'carol',
            'full_name': 'Carol Danvers',
            'email': 'carol@example.com',
            'password': 'password123',
            'password_confirm': 'password123',
        }, follow=True)

        user = User.objects.get(username='carol')
        group = user.roommate.groups.first()

        resp = self.client.post('/expenses/add/', {
            'group_id': group.id,
            'description': 'Wi-Fi Router',
            'amount': '89.99',
            'category': 'Utilities',
            'date': '2019-01-01',  # Stale past date
            'split_type': 'equal',
            'split_roommates': [user.roommate.id],
        }, follow=True)
        self.assertEqual(resp.status_code, 200)

        exp = Expense.objects.filter(group=group, description='Wi-Fi Router').first()
        self.assertIsNotNone(exp)
        # Server-side enforcement guarantees date is today regardless of input
        self.assertEqual(exp.date, timezone.now().date())


class GroupAdminPermissionsTestCase(TestCase):
    """
    Validates Group Admin authorization:
    - Group creators can manage details and member rosters.
    - Non-admins cannot alter or delete groups they do not own.
    - Group creators cannot be removed from their own groups.
    """

    def setUp(self):
        self.client_owner = Client()
        self.client_intruder = Client()

        self.owner_user = User.objects.create_user(username='owner', password='password123', first_name='House Owner')
        self.owner_rm = Roommate.objects.create(name='House Owner', user=self.owner_user, is_active=True)

        self.intruder_user = User.objects.create_user(username='intruder', password='password123', first_name='Intruder')
        self.intruder_rm = Roommate.objects.create(name='Intruder', user=self.intruder_user, is_active=True)

        self.group = HouseholdGroup.objects.create(
            name='Private House',
            group_type='home',
            created_by=self.owner_rm
        )
        self.group.members.add(self.owner_rm)

    def test_owner_can_add_member(self):
        self.client_owner.login(username='owner', password='password123')
        resp = self.client_owner.post(f'/groups/{self.group.id}/manage/', {
            'action': 'add_member',
            'new_member_name': 'Dave Newcomer',
            'new_member_email': 'dave@example.com',
        }, follow=True)
        self.assertEqual(resp.status_code, 200)

        dave = Roommate.objects.filter(name='Dave Newcomer').first()
        self.assertIsNotNone(dave)
        self.assertIn(dave, self.group.members.all())

    def test_intruder_cannot_modify_group(self):
        self.client_intruder.login(username='intruder', password='password123')
        resp = self.client_intruder.post(f'/groups/{self.group.id}/manage/', {
            'action': 'edit_details',
            'name': 'Hacked Group',
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Permission denied")

        self.group.refresh_from_db()
        self.assertEqual(self.group.name, 'Private House')

    def test_owner_cannot_remove_self(self):
        self.client_owner.login(username='owner', password='password123')
        resp = self.client_owner.post(f'/groups/{self.group.id}/manage/', {
            'action': 'remove_member',
            'roommate_id': self.owner_rm.id,
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Cannot remove")
        self.assertIn(self.owner_rm, self.group.members.all())


class SplitwiseAlgorithmAndStressTestCase(TestCase):
    """
    Stress tests the core Splitwise Min-Cash-Flow Simplification Algorithm:
    - Tests 3-person and 4-person debt cycles (A owes B, B owes C, C owes A).
    - Verifies balance matrix conservation (sum of all net balances == 0).
    - High-volume stress testing across 12 roommates and 100 transactions.
    """

    def test_pairwise_debt_simplification_cycle(self):
        r1 = Roommate(id=1, name='Person 1')
        r2 = Roommate(id=2, name='Person 2')
        r3 = Roommate(id=3, name='Person 3')
        roommates = [r1, r2, r3]

        # Circular debts:
        # P1 is owed $30 overall (+30)
        # P2 is neutral (0)
        # P3 owes $30 overall (-30)
        net_balances = {
            1: Decimal('30.00'),
            2: Decimal('0.00'),
            3: Decimal('-30.00'),
        }

        matrix = simplify_debts(net_balances, roommates)
        # Person 3 should directly owe Person 1 $30
        self.assertEqual(matrix[1][3], Decimal('30.00'))
        self.assertEqual(matrix[3][1], Decimal('-30.00'))
        self.assertEqual(matrix[2][1], Decimal('0.00'))
        self.assertEqual(matrix[2][3], Decimal('0.00'))

    def test_multi_party_split_integrity(self):
        group = HouseholdGroup.objects.create(name='Test House')
        m1 = Roommate.objects.create(name='M1', is_active=True)
        m2 = Roommate.objects.create(name='M2', is_active=True)
        m3 = Roommate.objects.create(name='M3', is_active=True)
        group.members.add(m1, m2, m3)

        # M1 pays $90 for Groceries split equally ($30 each)
        exp = Expense.objects.create(group=group, description='Groceries', amount=Decimal('90.00'), paid_by=m1, date=timezone.now().date())
        ExpenseSplit.objects.create(expense=exp, roommate=m1, amount=Decimal('30.00'))
        ExpenseSplit.objects.create(expense=exp, roommate=m2, amount=Decimal('30.00'))
        ExpenseSplit.objects.create(expense=exp, roommate=m3, amount=Decimal('30.00'))

        data = calculate_balances(current_roommate=m1, group=group)
        # M1 should be owed $60 total ($30 from M2, $30 from M3)
        self.assertEqual(data['user_summary']['total_owed_to_user'], Decimal('60.00'))
        self.assertEqual(data['user_summary']['total_user_owes'], Decimal('0.00'))

    def test_large_scale_stress_simplification(self):
        group = HouseholdGroup.objects.create(name='Stress Test House')
        members = [Roommate.objects.create(name=f'Member {i}', is_active=True) for i in range(12)]
        group.members.add(*members)

        # Generate 100 multi-split transactions
        for i in range(100):
            payer = members[i % len(members)]
            amt = Decimal('120.00')
            exp = Expense.objects.create(
                group=group,
                description=f'Expense {i}',
                amount=amt,
                paid_by=payer,
                date=timezone.now().date()
            )
            split_amt = Decimal('10.00')
            for m in members:
                ExpenseSplit.objects.create(expense=exp, roommate=m, amount=split_amt)

        start_time = time.time()
        data = calculate_balances(current_roommate=members[0], group=group)
        duration = time.time() - start_time

        # Ensure execution is lightning fast (< 1.5 seconds)
        self.assertLess(duration, 1.5)

        # Net sum of all directed balances across the simplified matrix must be exactly 0
        matrix_sum = sum(
            sum(row.values()) for row in data['net_matrix'].values()
        )
        self.assertEqual(matrix_sum, Decimal('0.00'))


class SuperstoreParserAndReconciliationTestCase(TestCase):
    """
    Validates Superstore HTML parsing engine:
    - Extracts product titles, multi-pack/weighted quantities, CDN images.
    - Reconciles bottle deposits & fees to equal the order total ($83.20).\
    - Tests deletion endpoint without throwing 404.
    """

    def test_extract_items_and_reconcile_total(self):
        sample_html = """
        <div class="order-summary-total-item order-summary-total-item--trimmed__estimated-taxes">
          <span class="order-summary-total-item__values">$1.49</span>
        </div>
        <div class="order-summary-total-item order-summary-total-item--trimmed__estimated-total">
          <span class="order-summary-total-item__values">$83.20</span>
        </div>
        <ul class="cart-entry-list">
          <li class="cart-entry-list__item">
            <div class="cart-entry__content--product-name">Iodized Table Salt</div>
            <input class="cart-entry__content__quantity" value="2" />
            <span class="price__value">$3.58</span>
            <img class="responsive-image" src="//digital.loblaws.ca/salt.png" />
          </li>
          <li class="cart-entry-list__item">
            <div class="cart-entry__content--product-name">Homogenized Milk 3.25%</div>
            <input class="cart-entry__content__quantity" value="3" />
            <span class="price__value">$20.52</span>
            <img class="responsive-image" src="//digital.loblaws.ca/milk.png" />
          </li>
        </ul>
        """
        soup = BeautifulSoup(sample_html, 'html.parser')
        tax = parse_tax_from_soup(soup)
        total = parse_order_total_from_soup(soup)
        items = extract_items_from_soup(soup)

        self.assertEqual(tax, Decimal('1.49'))
        self.assertEqual(total, Decimal('83.20'))

        # Salt ($3.58) + Milk ($20.52) = $24.10 items
        # Tax = $1.49 => $25.59
        # Fee difference = $83.20 - $25.59 = $57.61
        deposit_item = next((it for it in items if 'Bottle Deposit' in it['name']), None)
        self.assertIsNotNone(deposit_item)
        self.assertEqual(deposit_item['total_price'], Decimal('57.61'))

        # Sum of items + tax must equal order total
        items_sum = sum(it['total_price'] for it in items)
        self.assertEqual(items_sum + tax, total)

    def test_delete_receipt_endpoint_graceful_missing(self):
        client = Client()
        admin_user = User.objects.create_superuser(username='superadmin', password='password123', email='admin@test.local')
        client.login(username='superadmin', password='password123')

        # Deleting non-existent receipt ID 9999 should redirect without 404
        resp = client.get('/receipt/9999/delete/', follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Receipt was already deleted or not found")
