import io
import csv
from decimal import Decimal
from datetime import datetime
from django.utils import timezone
from django.db import transaction
from rcsscrapper.models import Roommate, Expense, ExpenseSplit, Receipt, ReceiptItem, HouseholdGroup


CATEGORY_MAP = {
    'Groceries': 'Groceries',
    'Dining out': 'Dining',
    'Restaurants': 'Dining',
    'Food and drink': 'Dining',
    'Rent': 'Rent',
    'Electricity': 'Utilities',
    'Water': 'Utilities',
    'Trash': 'Utilities',
    'TV/Phone/Internet': 'Utilities',
    'Utilities - Other': 'Utilities',
    'Utilities': 'Utilities',
    'Household supplies': 'Household',
    'Cleaning': 'Household',
    'Furniture': 'Household',
    'Maintenance': 'Household',
    'Movies': 'Entertainment',
    'Sports': 'Entertainment',
    'Entertainment': 'Entertainment',
    'Bus/train': 'Transportation',
    'Car': 'Transportation',
    'Taxi': 'Transportation',
    'Transportation - Other': 'Transportation',
    'Gas/fuel': 'Transportation',
    'Gifts': 'Gifts',
    'Payment': 'Payment',
    'General': 'General',
}


def sync_receipt_to_expense(receipt):
    """
    Syncs a processed or item-assigned Superstore Receipt into an Expense record.
    Calculates exact roommate splits (including equal tax and rounding adjustments)
    and stores them in ExpenseSplit among currently active roommates of the receipt's group.
    """
    if not receipt.items.exists():
        return None

    items = list(receipt.items.prefetch_related('shares__roommate'))
    assigned_count = sum(1 for i in items if i.is_assigned)
    if assigned_count == 0:
        return None

    if not receipt.group:
        from rcsscrapper.utils import ensure_groups
        receipt.group = ensure_groups()
        receipt.save()

    target_group = receipt.group
    group_members = list(target_group.members.filter(is_active=True)) if target_group else []
    roommates = group_members if group_members else list(Roommate.objects.filter(is_active=True))
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
    # Allocate penny rounding difference to payer (Het or first roommate)
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
                'group': target_group,
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


def calculate_balances(current_roommate=None, group=None):
    """
    Calculates household balances and simplified debts between active roommates in a group.
    Returns:
      - net_matrix: simplified pairwise balance matrix (positive = B owes A)
      - user_summary: summary for current_roommate (total_net, total_owed_to_user, total_user_owes, friend_balances)
      - all_roommates: list of group members (or active Roommate objects)
      - expenses: chronological list of expenses in this group
      - group: the group object
    """
    if group:
        group_members = list(group.members.all())
        active_roommates = [r for r in group_members if r.is_active]
        if not active_roommates:
            active_roommates = group_members
        expenses_qs = Expense.objects.filter(group=group)
    else:
        active_roommates = list(Roommate.objects.filter(is_active=True))
        expenses_qs = Expense.objects.all()

    all_roommates_all = list(Roommate.objects.all())
    net_balances = {r.id: Decimal('0.00') for r in all_roommates_all}

    expenses = list(
        expenses_qs.select_related('paid_by', 'payment_to', 'receipt')
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
        'group': group,
    }


def resolve_roommate_for_name(raw_name, target_group=None):
    clean_name = raw_name.replace('(removed)', '').replace('(Removed)', '').strip()
    first_token = clean_name.split()[0] if clean_name else 'Unknown'

    rm = Roommate.objects.filter(name__iexact=clean_name).first()
    if not rm:
        rm = Roommate.objects.filter(name__iexact=first_token).first()
    if not rm:
        rm = Roommate.objects.filter(name__istartswith=first_token).first()
    if not rm:
        rm = Roommate.objects.create(name=clean_name, is_active=True)

    if target_group and not target_group.members.filter(id=rm.id).exists():
        target_group.members.add(rm)

    return rm


def import_and_clean_splitwise_csv(file_source, target_group, clean_to_cad=True, filter_zero_members=True, replace_existing=False):
    """
    Parses, cleans, and imports a Splitwise CSV export into the target group.
    - Standardizes currency units to CAD without distorting numeric dollar amounts.
    - Maps/creates roommates and enrolls them as members of target_group.
    - Atomically creates Expense and ExpenseSplit records.
    - Returns summary dictionary and cleaned CAD CSV content for download.
    """
    if isinstance(file_source, str):
        with open(file_source, 'r', encoding='utf-8-sig', errors='replace') as f:
            content = f.read()
    elif hasattr(file_source, 'read'):
        raw = file_source.read()
        content = raw.decode('utf-8-sig', errors='replace') if isinstance(raw, bytes) else raw
    else:
        content = str(file_source)

    reader = csv.reader(io.StringIO(content))
    header = None
    all_data_rows = []

    for row in reader:
        if not row:
            continue
        if len(row) >= 5 and row[0].strip().lower() == 'date':
            header = row
            break

    if not header:
        return {
            'success': False,
            'error': 'Invalid CSV: Could not find header row starting with "Date".'
        }

    raw_user_cols = header[5:]
    if not raw_user_cols:
        return {
            'success': False,
            'error': 'Invalid CSV: No roommate balance columns found after column 5.'
        }

    # Read remaining rows
    for row in reader:
        if not row or not row[0].strip():
            continue
        if len(row) > 1 and row[1].strip() == 'Total balance':
            continue
        all_data_rows.append(row)

    # Detect user activity
    active_user_indices = []
    user_totals = {}
    for idx, u in enumerate(raw_user_cols):
        col_total = Decimal('0.00')
        for r in all_data_rows:
            if len(r) > 5 + idx and r[5 + idx].strip():
                try:
                    col_total += abs(Decimal(r[5 + idx].strip()))
                except Exception:
                    pass
        user_totals[idx] = col_total
        if not filter_zero_members or col_total > Decimal('0.00'):
            active_user_indices.append(idx)

    if not active_user_indices:
        active_user_indices = list(range(len(raw_user_cols)))

    # Resolve roommates for active columns
    col_to_roommate = {}
    for idx in active_user_indices:
        u_name = raw_user_cols[idx]
        rm = resolve_roommate_for_name(u_name, target_group=target_group)
        col_to_roommate[idx] = rm

    # Build cleaned CSV
    cleaned_rows = []
    cleaned_header = header[:5] + [raw_user_cols[i] for i in active_user_indices]
    if clean_to_cad:
        cleaned_header[4] = 'Currency (CAD)'
    cleaned_rows.append(cleaned_header)

    transactions_to_import = []

    for row_idx, row in enumerate(all_data_rows, 1):
        date_str = row[0].strip()
        desc = row[1].strip() if len(row) > 1 else 'Shared Expense'
        cat = row[2].strip() if len(row) > 2 else 'General'
        cost_str = row[3].strip() if len(row) > 3 else '0.00'

        try:
            for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y', '%Y/%m/%d'):
                try:
                    date_val = datetime.strptime(date_str, fmt).date()
                    break
                except ValueError:
                    pass
            else:
                date_val = timezone.now().date()
            cost = Decimal(cost_str) if cost_str else Decimal('0.00')
        except Exception:
            continue

        cleaned_row = list(row[:5])
        if clean_to_cad:
            cleaned_row[4] = 'CAD'
        user_vals = {}
        for idx in active_user_indices:
            val_str = row[5 + idx].strip() if len(row) > 5 + idx else '0.00'
            try:
                val = Decimal(val_str) if val_str else Decimal('0.00')
            except Exception:
                val = Decimal('0.00')
            user_vals[idx] = val
            cleaned_row.append(f"{val:.2f}")

        cleaned_rows.append(cleaned_row)

        pos_cols = [idx for idx, v in user_vals.items() if v > Decimal('0.00')]
        neg_cols = [idx for idx, v in user_vals.items() if v < Decimal('0.00')]

        if not pos_cols and not neg_cols:
            continue

        mapped_cat = CATEGORY_MAP.get(cat, 'General')

        if cat.lower() == 'payment' or 'payment' in desc.lower() or 'paid' in desc.lower():
            if len(pos_cols) >= 1 and len(neg_cols) >= 1:
                payer = col_to_roommate[pos_cols[0]]
                recipient = col_to_roommate[neg_cols[0]]
                pay_amt = user_vals[pos_cols[0]] if user_vals[pos_cols[0]] > Decimal('0.00') else cost
                transactions_to_import.append({
                    'type': 'payment',
                    'date': date_val,
                    'description': desc or f"{payer.name} paid {recipient.name}",
                    'category': 'General',
                    'amount': abs(pay_amt),
                    'payer': payer,
                    'recipient': recipient,
                })
        else:
            if len(pos_cols) >= 1:
                payer = col_to_roommate[pos_cols[0]]
                payer_credit = user_vals[pos_cols[0]]
                payer_share = cost - payer_credit

                splits = {}
                for borrower_idx in neg_cols:
                    borrower_rm = col_to_roommate[borrower_idx]
                    splits[borrower_rm] = abs(user_vals[borrower_idx])

                if payer_share > Decimal('0.00'):
                    splits[payer] = payer_share

                transactions_to_import.append({
                    'type': 'expense',
                    'date': date_val,
                    'description': desc or "Shared Expense",
                    'category': mapped_cat,
                    'amount': cost,
                    'payer': payer,
                    'splits': splits,
                })

    # Perform atomic database writes
    expenses_created = 0
    payments_created = 0

    with transaction.atomic():
        if replace_existing:
            Expense.objects.filter(group=target_group, receipt__isnull=True).delete()

        for tx in transactions_to_import:
            if tx['type'] == 'payment':
                Expense.objects.create(
                    group=target_group,
                    description=tx['description'],
                    amount=tx['amount'],
                    category='General',
                    paid_by=tx['payer'],
                    payment_to=tx['recipient'],
                    date=tx['date'],
                    is_payment=True,
                    notes='Imported from Splitwise'
                )
                payments_created += 1
            else:
                exp = Expense.objects.create(
                    group=target_group,
                    description=tx['description'],
                    amount=tx['amount'],
                    category=tx['category'],
                    paid_by=tx['payer'],
                    date=tx['date'],
                    is_payment=False,
                    notes='Imported from Splitwise'
                )
                for rm_obj, split_amt in tx['splits'].items():
                    ExpenseSplit.objects.create(
                        expense=exp,
                        roommate=rm_obj,
                        amount=split_amt
                    )
                expenses_created += 1

    # Generate cleaned CSV string
    csv_out = io.StringIO()
    writer = csv.writer(csv_out)
    writer.writerows(cleaned_rows)
    cleaned_csv_content = csv_out.getvalue()

    return {
        'success': True,
        'group_name': target_group.name,
        'expenses_created': expenses_created,
        'payments_created': payments_created,
        'total_imported': expenses_created + payments_created,
        'members_count': target_group.members.count(),
        'cleaned_csv_content': cleaned_csv_content,
    }
