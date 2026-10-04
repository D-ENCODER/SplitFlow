import os
import re
from datetime import datetime
from decimal import Decimal
from bs4 import BeautifulSoup
from django.conf import settings
from django.utils import timezone
from rcsscrapper.models import Receipt, ReceiptItem, Roommate

INBOX_DIR = os.path.join(settings.BASE_DIR, 'rcsscrapper', 'receipts_inbox')


def ensure_roommates():
    from django.contrib.auth.models import User

    profiles = [
        {'name': 'Het', 'username': 'het', 'is_me': True, 'is_admin': True},
        {'name': 'Ruchit', 'username': 'ruchit', 'is_me': False, 'is_admin': False},
        {'name': 'Tirth', 'username': 'tirth', 'is_me': False, 'is_admin': False},
        {'name': 'Maurya', 'username': 'maurya', 'is_me': False, 'is_admin': False},
    ]

    for p in profiles:
        user = User.objects.filter(username=p['username']).first()
        if not user:
            user = User.objects.create_user(
                username=p['username'],
                email=f"{p['username']}@splitflow.local",
                password='admin'
            )
            if p['is_admin']:
                user.is_superuser = True
                user.is_staff = True
                user.save()

        rm, _ = Roommate.objects.get_or_create(name=p['name'])
        if rm.user != user or rm.is_me != p['is_me'] or not rm.email or not rm.is_active:
            rm.user = user
            rm.is_me = p['is_me']
            rm.email = user.email
            rm.is_active = True
            rm.save()


def parse_tax_from_soup(soup):
    tax_el = soup.select_one(
        '.order-summary-total-item--trimmed__estimated-taxes .order-summary-total-item__values'
    )
    if tax_el:
        matches = re.findall(r'\d+\.\d{2}', tax_el.get_text())
        if matches:
            return Decimal(matches[-1])

    tax_label = soup.find(string=re.compile(r'Est\.\s*Taxes|Estimated Tax|Taxes|GST|PST|HST', re.I))
    if tax_label and tax_label.find_parent():
        row = tax_label.find_parent(class_='order-summary-total-item') or tax_label.find_parent().parent
        matches = re.findall(r'\$\s*(\d+\.\d{2})', row.get_text())
        if matches:
            return Decimal(matches[-1])
    return Decimal('0.00')


def parse_order_total_from_soup(soup):
    # Matches both the bottom Payment Summary Total ($50.61) and top Summary card Total ($50.61)
    for selector in [
        '.order-summary-total-item--trimmed__estimated-total .order-summary-total-item__values',
        '.inprogress-payment-summary__total .order-summary-total-item__values',
        '.preparing-order-summary .order-summary-sub-total__values',
    ]:
        el = soup.select_one(selector)
        if el:
            matches = re.findall(r'\d+\.\d{2}', el.get_text())
            if matches:
                return Decimal(matches[-1])
    return None


def sync_receipts_folder():
    ensure_roommates()
    os.makedirs(INBOX_DIR, exist_ok=True)

    for fname in os.listdir(INBOX_DIR):
        if not fname.endswith('.html'):
            continue

        filepath = os.path.join(INBOX_DIR, fname)
        mtime = timezone.make_aware(datetime.fromtimestamp(os.path.getmtime(filepath)))

        with open(filepath, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f.read(), 'html.parser')

        tax_val = parse_tax_from_soup(soup)
        order_total = parse_order_total_from_soup(soup)
        time_el = soup.select_one('.order-details-pickup__content__detail__info__time')
        order_date_str = time_el.get_text(strip=True) if time_el else ''

        receipt = Receipt.objects.filter(filename=fname).first()
        if receipt and receipt.items.count() == 0 and len(soup.select('li.cart-entry-list__item')) > 0:
            receipt.delete()
            receipt = None

        if not receipt:
            receipt = Receipt.objects.create(
                filename=fname,
                tax_amount=tax_val,
                file_modified_at=mtime,
                order_date_str=order_date_str
            )
            for li in soup.select('li.cart-entry-list__item'):
                name_el = li.select_one('.cart-entry__content--product-name')
                qty_el = li.select_one('input.cart-entry__content__quantity')
                price_el = (
                    li.select_one('.cart-entry__content__price--total .price__value')
                    or li.select_one('.price--total .price__value')
                    or li.select_one('.price__value')
                )
                img_el = li.select_one('img.responsive-image')

                if name_el and price_el:
                    price_matches = re.findall(r'\d+\.\d{2}', price_el.get_text())
                    if not price_matches:
                        continue
                    item_price = Decimal(price_matches[0])
                    item_name = name_el.get_text(strip=True)
                    if item_price == Decimal('0.00') and 'STAMP' in item_name.upper():
                        continue

                    ReceiptItem.objects.create(
                        receipt=receipt,
                        name=item_name,
                        quantity_str=qty_el['value'] if qty_el and qty_el.has_attr('value') else '1',
                        total_price=item_price,
                        image_url=img_el['src'] if img_el and img_el.has_attr('src') else None
                    )

        # Reconcile total: if Superstore's $50.61 Total > $48.08 items_sum,
        # set tax_amount = $50.61 - $48.08 = $2.53 (which includes $1.54 tax + $0.99 weight diff)
        items_sum = sum((i.total_price for i in receipt.items.all()), Decimal('0.00'))
        if order_total and order_total > items_sum:
            target_tax = (order_total - items_sum).quantize(Decimal('0.01'))
        else:
            target_tax = tax_val

        updated = False
        if receipt.tax_amount != target_tax:
            receipt.tax_amount = target_tax
            updated = True
        if receipt.file_modified_at != mtime:
            receipt.file_modified_at = mtime
            updated = True
        if order_date_str and receipt.order_date_str != order_date_str:
            receipt.order_date_str = order_date_str
            updated = True
        if updated:
            receipt.save()
