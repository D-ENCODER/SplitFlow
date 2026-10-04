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

    # 1. Ensure system administrator account exists
    admin_user = User.objects.filter(username='admin').first()
    if not admin_user:
        admin_user = User.objects.create_superuser(
            username='admin',
            email='admin@splitflow.local',
            password='Hado#33Sokastsui'
        )
    else:
        if not admin_user.is_superuser or not admin_user.is_staff:
            admin_user.is_superuser = True
            admin_user.is_staff = True
            admin_user.save()

    # Admin profile linked to admin user
    admin_rm, _ = Roommate.objects.get_or_create(
        name='Admin',
        defaults={'email': 'admin@splitflow.local', 'is_me': False, 'is_active': False, 'user': admin_user}
    )
    if admin_rm.user != admin_user:
        admin_rm.user = admin_user
        admin_rm.save()

    # 2. Ensure primary household roommates exist
    profiles = [
        {'name': 'Het', 'username': 'het', 'is_me': True},
        {'name': 'Ruchit', 'username': 'ruchit', 'is_me': False},
        {'name': 'Tirth', 'username': 'tirth', 'is_me': False},
        {'name': 'Maurya', 'username': 'maurya', 'is_me': False},
    ]

    for p in profiles:
        user = User.objects.filter(username=p['username']).first()
        if not user:
            user = User.objects.create_user(
                username=p['username'],
                email=f"{p['username']}@splitflow.local",
                password='admin'
            )

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
                order_date_str=order_date_str,
                file_modified_at=mtime
            )

            for li in soup.select('li.cart-entry-list__item'):
                title_el = li.select_one('.item-details__item-name, [data-track="product-title"], .product-name')
                name = title_el.get_text(strip=True) if title_el else "Unknown Item"

                qty_el = li.select_one('.item-details__quantity, [data-track="product-qty"]')
                qty = qty_el.get_text(strip=True) if qty_el else "1"

                price_el = li.select_one('.selling-price-list__item__price--now .price__value, .price__value')
                price = Decimal('0.00')
                if price_el:
                    clean_str = re.sub(r'[^\d.]', '', price_el.get_text(strip=True))
                    if clean_str:
                        try:
                            price = Decimal(clean_str)
                        except Exception:
                            pass

                img_el = li.select_one('img.responsive-image__image')
                image_url = img_el['src'] if img_el and img_el.has_attr('src') else None

                ReceiptItem.objects.create(
                    receipt=receipt,
                    name=name,
                    quantity_str=qty,
                    total_price=price,
                    image_url=image_url
                )
        else:
            if receipt.tax_amount == Decimal('0.00') and tax_val > Decimal('0.00'):
                receipt.tax_amount = tax_val
                receipt.save()
            if not receipt.order_date_str and order_date_str:
                receipt.order_date_str = order_date_str
                receipt.save()
            if receipt.file_modified_at != mtime:
                receipt.file_modified_at = mtime
                receipt.save()
