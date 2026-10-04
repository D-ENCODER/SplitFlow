from decimal import Decimal
from django.utils import timezone
from django.db import transaction
from rcsscrapper.models import Roommate, Expense, ExpenseSplit, Receipt, ReceiptItem


def sync_receipt_to_expense(receipt):
    """
    Syncs a processed or item-assigned Superstore Receipt into an Expense record.
    Calculates exact roommate splits (including equal tax and rounding adjustments)
    and stores them in ExpenseSplit among currently active roommates.
    """
    if not receipt.items.exists():
        return None

    items = list(receipt.items.prefetch_related('shares__roommate'))
    assigned_count = sum(1 for i in items if i.is_assigned)
    if assigned_count == 0:
        return None

    roommates = list(Roommate.objects.filter(is_active=True))
    num_roommates = len(roommates)
    if num_roommates == 0:
        return None

    # Calculate item shares and equal tax share
    r_items_exact = {r: Decimal('0.0000') for r in roommates}
    actual_items_sum = Decimal('0.00')

    for item in items:
        actual_items_sum += item.total_price
        total_units = sum(s.units for s in item.shares.all())
        if total_units > 0:
            for share in item.shares.all():
                if share.roommate in r_items_exact:
                    exact_cost = item.total_price * (share.units / total_units)
                    r_items_exact[share.roommate] += exact_cost

    exact_tax_per_person = (
        (receipt.tax_amount / Decimal(num_roommates))
        if num_roommates > 0 else Decimal('0.00')
    )

    roommate_totals = {}
    for r in roommates:
        person_total = (r_items_exact[r] + exact_tax_per_person).quantize(Decimal('0.01'))
        roommate_totals[r] = person_total

    true_receipt_total = (actual_items_sum + receipt.tax_amount).quantize(Decimal('0.01'))
    # Allocate penny rounding difference to payer (Het)
    diff = true_receipt_total - sum(roommate_totals.values())
    if diff != Decimal('0.00'):
        payer_rm = next((r for r in roommates if r.is_me), roommates[0])
        roommate_totals[payer_rm] += diff

    payer = next((r for r in roommates if r.is_me), roommates[0])
    date_val = receipt.file_modified_at.date() if receipt.file_modified_at else timezone.now().date()
    desc = f"Superstore: {receipt.order_date_str}" if receipt.order_date_str else f"Superstore: {receipt.filename}"

    with transaction.atomic():
        expense, _ = Expense.objects.update_or_create(
            receipt=receipt,
            defaults={
                'description': desc,
                'amount': true_receipt_total,
                'category': 'Groceries',
                'date': date_val,
                'paid_by': payer,
                'is_payment': False,
            }
        )

        ExpenseSplit.objects.filter(expense=expense).delete()
        for r, amt in roommate_totals.items():
            ExpenseSplit.objects.create(
                expense=expense,
                roommate=r,
                amount=amt
            )

    return expense


def sync_all_receipts():
    """Sync all receipts that have assigned items into Expense records."""
    for r in Receipt.objects.all():
        if r.items.filter(is_assigned=True).exists():
            sync_receipt_to_expense(r)


def simplify_debts(net_balances, active_roommates):
    """
    Minimizes transaction graph among active roommates using the min-cash-flow algorithm (Splitwise Simplify Debts).
    Returns simplified_matrix: simplified_matrix[A][B] is how much B owes A.
    """
    simplified_matrix = {r.id: {other.id: Decimal('0.00') for other in active_roommates} for r in active_roommates}

    creditors = [[r.id, net_balances.get(r.id, Decimal('0.00'))] for r in active_roommates if net_balances.get(r.id, Decimal('0.00')) > Decimal('0.00')]
    debtors = [[r.id, -net_balances.get(r.id, Decimal('0.00'))] for r in active_roommates if net_balances.get(r.id, Decimal('0.00')) < Decimal('0.00')]

    creditors.sort(key=lambda x: x[1], reverse=True)
    debtors.sort(key=lambda x: x[1], reverse=True)

    c_idx = 0
    d_idx = 0
    while c_idx < len(creditors) and d_idx < len(debtors):
        c_id, c_amt = creditors[c_idx]
        d_id, d_amt = debtors[d_idx]

        settle_amt = min(c_amt, d_amt)
        # d_id owes c_id settle_amt
        simplified_matrix[c_id][d_id] += settle_amt
        simplified_matrix[d_id][c_id] -= settle_amt

        creditors[c_idx][1] -= settle_amt
        debtors[d_idx][1] -= settle_amt

        if creditors[c_idx][1] <= Decimal('0.001'):
            c_idx += 1
        if debtors[d_idx][1] <= Decimal('0.001'):
            d_idx += 1

    return simplified_matrix


def calculate_balances(current_roommate=None):
    """
    Calculates household balances and simplified debts between active roommates.
    Returns:
      - net_matrix: simplified pairwise balance matrix (positive = B owes A)
      - user_summary: summary for current_roommate (total_net, total_owed_to_user, total_user_owes, friend_balances)
      - all_roommates: list of currently active Roommate objects
      - expenses: chronological list of all expenses
    """
    all_roommates_all = list(Roommate.objects.all())
    active_roommates = list(Roommate.objects.filter(is_active=True))

    # Calculate true net balance for every roommate across all historical transactions
    net_balances = {r.id: Decimal('0.00') for r in all_roommates_all}

    expenses = list(
        Expense.objects.select_related('paid_by', 'payment_to', 'receipt')
        .prefetch_related('splits__roommate')
        .order_by('-date', '-created_at')
    )

    for exp in expenses:
        if exp.is_payment:
            if exp.paid_by_id and exp.payment_to_id and exp.paid_by_id != exp.payment_to_id:
                net_balances[exp.paid_by_id] += exp.amount
                net_balances[exp.payment_to_id] -= exp.amount
        else:
            payer_id = exp.paid_by_id
            for split in exp.splits.all():
                borrower_id = split.roommate_id
                if borrower_id != payer_id:
                    net_balances[payer_id] += split.amount
                    net_balances[borrower_id] -= split.amount

    # Splitwise Simplify Debts among active roommates
    simplified_matrix = simplify_debts(net_balances, active_roommates)

    user_summary = None
    if current_roommate:
        me_id = current_roommate.id
        total_owed_to_me = Decimal('0.00')
        total_i_owe = Decimal('0.00')
        friend_balances = []

        for other in active_roommates:
            if other.id == me_id:
                continue
            bal = simplified_matrix.get(me_id, {}).get(other.id, Decimal('0.00')).quantize(Decimal('0.01'))
            if bal > Decimal('0.00'):
                total_owed_to_me += bal
                status = 'owes_you'
            elif bal < Decimal('0.00'):
                total_i_owe += abs(bal)
                status = 'you_owe'
            else:
                status = 'settled'

            friend_balances.append({
                'roommate': other,
                'amount': abs(bal),
                'raw_balance': bal,
                'status': status,
            })

        net_total = net_balances.get(me_id, Decimal('0.00')).quantize(Decimal('0.01'))
        user_summary = {
            'total_net': net_total,
            'total_owed_to_user': total_owed_to_me.quantize(Decimal('0.01')),
            'total_user_owes': total_i_owe.quantize(Decimal('0.01')),
            'friend_balances': friend_balances,
        }

    return {
        'net_matrix': simplified_matrix,
        'user_summary': user_summary,
        'all_roommates': active_roommates,
        'expenses': expenses,
    }
