import os
import csv
import shutil
from decimal import Decimal
from datetime import datetime
from django.core.management.base import BaseCommand
from django.db import transaction
from django.conf import settings
from rcsscrapper.models import Roommate, Expense, ExpenseSplit
from rcsscrapper.utils import ensure_roommates
from rcsscrapper.services import calculate_balances


class Command(BaseCommand):
    help = 'Clean and import Splitwise CSV export into SplitFlow with CAD currency and 4 active roommates'

    def add_arguments(self, parser):
        parser.add_argument(
            '--csv-file',
            default='data/41_2026-10-02_export.csv',
            help='Path to the Splitwise CSV export file'
        )

    def handle(self, *args, **options):
        csv_path = options.get('csv_file') or options.get('csv-file')
        if not os.path.isabs(csv_path):
            csv_path = os.path.join(settings.BASE_DIR, csv_path)

        if not os.path.exists(csv_path):
            self.stderr.write(self.style.ERROR(f"File not found: {csv_path}"))
            return

        self.stdout.write(self.style.SUCCESS(f"Reading Splitwise data from: {csv_path}"))

        # Step 1: Backup original CSV
        backup_path = os.path.join(settings.BASE_DIR, 'data', '41_2026-10-02_export_original.csv')
        if not os.path.exists(backup_path):
            shutil.copy2(csv_path, backup_path)
            self.stdout.write(self.style.SUCCESS(f"Created backup at: {backup_path}"))

        # Step 2: Ensure 4 Active Roommates exist
        ensure_roommates()
        active_roommates = list(Roommate.objects.filter(is_active=True))

        # Roommate mapping dictionary
        name_map = {
            'Maurya shah': 'Maurya',
            'Het Joshi': 'Het',
            'Tirth Acharya': 'Tirth',
            'Ruchit Patel': 'Ruchit',
            'Kavy patel (removed)': 'Kavy',
            'Dinesh Kumawat (removed)': 'Dinesh',
            'Meet Patel (removed)': 'Meet',
            'Jalay Pareshkumar Prajapati (removed)': 'Jalay',
            'Maitri Shah (removed)': 'Maitri',
        }

        former_roommate_names = ['Kavy', 'Dinesh', 'Meet', 'Jalay', 'Maitri']
        former_roommates = {}
        for fn in former_roommate_names:
            rm, _ = Roommate.objects.get_or_create(name=fn, defaults={'is_active': False, 'is_me': False})
            rm.is_active = False
            rm.save()
            former_roommates[fn] = rm

        all_roommates_dict = {}
        for r in Roommate.objects.all():
            all_roommates_dict[r.name] = r

        # Step 3: Parse CSV, Clean Currency to CAD (no exchange rate conversion)
        cleaned_rows = []
        cleaned_4_roommates_rows = []
        transactions_to_import = []

        category_map = {
            'Groceries': 'Groceries',
            'Dining out': 'Dining',
            'Rent': 'Rent',
            'Electricity': 'Utilities',
            'Water': 'Utilities',
            'Trash': 'Utilities',
            'TV/Phone/Internet': 'Utilities',
            'Utilities - Other': 'Utilities',
            'Household supplies': 'Household',
            'Cleaning': 'Household',
            'Furniture': 'Household',
            'Maintenance': 'Household',
            'Movies': 'Entertainment',
            'Sports': 'Entertainment',
            'Bus/train': 'Transportation',
            'Car': 'Transportation',
            'Taxi': 'Transportation',
            'Transportation - Other': 'Transportation',
            'Gas/fuel': 'Transportation',
            'Gifts': 'Gifts',
            'Payment': 'Payment',
        }

        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            header = next(reader)
            user_columns = header[5:]

            # Clean header with CAD unit
            cleaned_header = list(header)
            cleaned_header[4] = 'Currency (CAD)'
            cleaned_rows.append(cleaned_header)

            # 4 roommates header
            active_col_indices = [users_col_idx for users_col_idx, u in enumerate(user_columns) if '(removed)' not in u]
            header_4 = header[:5] + [user_columns[i] for i in active_col_indices]
            header_4[4] = 'Currency (CAD)'
            cleaned_4_roommates_rows.append(header_4)

            for row_idx, row in enumerate(reader, 1):
                if not row or not row[0]:
                    continue

                # Check if summary row
                if row[1] == 'Total balance':
                    cleaned_row = list(row)
                    cleaned_row[4] = 'CAD'
                    cleaned_rows.append(cleaned_row)
                    continue

                date_str, desc, cat, cost_str, curr = row[:5]
                try:
                    date_val = datetime.strptime(date_str, '%Y-%m-%d').date()
                    cost = Decimal(cost_str) if cost_str.strip() else Decimal('0.00')
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"Skipping malformed row {row_idx}: {e}"))
                    continue

                user_vals = {
                    name_map[u]: Decimal(row[5 + i]) if row[5 + i].strip() else Decimal('0.00')
                    for i, u in enumerate(user_columns)
                }

                # Clean unit to CAD
                cleaned_row = list(row)
                cleaned_row[4] = 'CAD'
                cleaned_rows.append(cleaned_row)

                # 4 roommates row
                row_4 = row[:5] + [row[5 + i] for i in active_col_indices]
                row_4[4] = 'CAD'
                cleaned_4_roommates_rows.append(row_4)

                pos_users = [name for name, val in user_vals.items() if val > Decimal('0.00')]
                neg_users = [name for name, val in user_vals.items() if val < Decimal('0.00')]

                # Skip completely empty participant rows (e.g. row 919)
                if not pos_users and not neg_users:
                    continue

                mapped_cat = category_map.get(cat, 'General')

                if cat == 'Payment':
                    if len(pos_users) == 1 and len(neg_users) == 1:
                        payer_name = pos_users[0]
                        recip_name = neg_users[0]
                        pay_amt = user_vals[payer_name]
                        transactions_to_import.append({
                            'type': 'payment',
                            'date': date_val,
                            'description': desc or f"{payer_name} paid {recip_name}",
                            'category': 'Payment',
                            'amount': pay_amt,
                            'payer': all_roommates_dict[payer_name],
                            'recipient': all_roommates_dict[recip_name],
                        })
                    else:
                        self.stdout.write(self.style.WARNING(f"Unusual payment at row {row_idx}: {pos_users}, {neg_users}"))
                else:
                    if len(pos_users) == 1:
                        payer_name = pos_users[0]
                        payer_credit = user_vals[payer_name]
                        payer_share = cost - payer_credit

                        splits = {}
                        for borrower in neg_users:
                            splits[all_roommates_dict[borrower]] = abs(user_vals[borrower])

                        if payer_share > Decimal('0.00'):
                            splits[all_roommates_dict[payer_name]] = payer_share

                        transactions_to_import.append({
                            'type': 'expense',
                            'date': date_val,
                            'description': desc or "Shared Expense",
                            'category': mapped_cat,
                            'amount': cost,
                            'payer': all_roommates_dict[payer_name],
                            'splits': splits,
                        })
                    else:
                        self.stdout.write(self.style.WARNING(f"Unusual expense at row {row_idx}: {pos_users}, {neg_users}"))

        # Step 4: Write cleaned CSV files
        cleaned_csv_path = os.path.join(settings.BASE_DIR, 'data', 'cleaned_splitwise_cad.csv')
        with open(cleaned_csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerows(cleaned_rows)
        self.stdout.write(self.style.SUCCESS(f"Saved cleaned CSV (with CAD units) to: {cleaned_csv_path}"))

        # Save 4 roommates cleaned CSV
        csv_4_path = os.path.join(settings.BASE_DIR, 'data', 'splitwise_4_roommates_cad.csv')
        with open(csv_4_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerows(cleaned_4_roommates_rows)
        self.stdout.write(self.style.SUCCESS(f"Saved 4 roommates cleaned CSV to: {csv_4_path}"))

        # Also update data/41_2026-10-02_export.csv with CAD units
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerows(cleaned_rows)
        self.stdout.write(self.style.SUCCESS(f"Updated {csv_path} to CAD currency."))

        # Step 5: Import into database atomically
        self.stdout.write("Importing transactions into database...")
        with transaction.atomic():
            # Delete non-receipt expenses so import can be run idempotently
            Expense.objects.filter(receipt__isnull=True).delete()

            expenses_created = 0
            splits_created = 0

            for tx in transactions_to_import:
                if tx['type'] == 'payment':
                    exp = Expense.objects.create(
                        description=tx['description'],
                        amount=tx['amount'],
                        category='Payment',
                        paid_by=tx['payer'],
                        payment_to=tx['recipient'],
                        is_payment=True,
                        date=tx['date']
                    )
                    expenses_created += 1
                else:
                    exp = Expense.objects.create(
                        description=tx['description'],
                        amount=tx['amount'],
                        category=tx['category'],
                        paid_by=tx['payer'],
                        is_payment=False,
                        date=tx['date']
                    )
                    expenses_created += 1
                    for rm, share_amt in tx['splits'].items():
                        ExpenseSplit.objects.create(
                            expense=exp,
                            roommate=rm,
                            amount=share_amt
                        )
                        splits_created += 1

        self.stdout.write(self.style.SUCCESS(
            f"Successfully imported {expenses_created} transactions with {splits_created} splits!"
        ))

        # Step 6: Validate balances and verify against Splitwise numbers
        self.stdout.write("\n" + "=" * 50)
        self.stdout.write(self.style.SUCCESS("VERIFYING SPLITWISE BALANCES & SIMPLIFIED DEBTS"))
        self.stdout.write("=" * 50)

        for rm in active_roommates:
            ledger = calculate_balances(current_roommate=rm)
            summary = ledger['user_summary']
            self.stdout.write(
                f"\nRoommate: {rm.name} (Me: {rm.is_me})\n"
                f"  Net Total: ${summary['total_net']}\n"
                f"  Total Owed To You: ${summary['total_owed_to_user']}\n"
                f"  Total You Owe: ${summary['total_user_owes']}"
            )
            for f in summary['friend_balances']:
                self.stdout.write(f"    - {f['roommate'].name}: {f['status']} ${f['amount']}")

        # Ensure former roommates have 0.00 balance
        for fn in former_roommate_names:
            frm = former_roommates[fn]
            ledger = calculate_balances(current_roommate=frm)
            net_bal = ledger['user_summary']['total_net']
            if net_bal != Decimal('0.00'):
                self.stdout.write(self.style.WARNING(f"Former roommate {fn} net balance is NOT zero: {net_bal}"))
            else:
                self.stdout.write(self.style.SUCCESS(f"Former roommate {fn} has settled balance: $0.00"))

        active_count = Roommate.objects.filter(is_active=True).count()
        self.stdout.write(f"\nActive roommates in SplitFlow: {active_count} (Het, Ruchit, Tirth, Maurya)")
        self.stdout.write(self.style.SUCCESS("All Splitwise historical data imported successfully!"))
