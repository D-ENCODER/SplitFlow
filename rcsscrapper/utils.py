import os
import re
import json
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
            password='Hado#33Sokatsui'
        )
    else:
        if not admin_user.is_superuser or not admin_user.is_staff:
            admin_user.is_superuser = True
            admin_user.is_staff = True
            admin_user.save()

    # Admin profile linked to admin user
    admin_rm = getattr(admin_user, 'roommate', None)
    if not admin_rm:
        admin_rm = Roommate.objects.filter(name='Admin', user__isnull=True).first()
        if not admin_rm:
            admin_rm = Roommate.objects.create(
                name='Admin',
                user=admin_user,
                email=admin_user.email or 'admin@splitflow.local',
                is_me=False,
                is_active=False
            )
        else:
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

        # Look up by user's existing roommate link first (prevents duplicate key errors if renamed)
        rm = getattr(user, 'roommate', None)
        if not rm:
            # Check if there is an unlinked roommate with this name
            rm = Roommate.objects.filter(name=p['name'], user__isnull=True).first()
            if not rm:
                rm = Roommate.objects.create(
                    name=p['name'],
                    user=user,
                    email=user.email,
                    is_me=p['is_me'],
                    is_active=True
                )
            else:
                rm.user = user
                rm.is_me = p['is_me']
                rm.email = user.email
                rm.is_active = True
                rm.save()

    # 3. Ensure primary household group exists and is populated
    ensure_groups()


def ensure_groups():
    from rcsscrapper.models import HouseholdGroup, Roommate, Expense, Receipt
    group, _ = HouseholdGroup.objects.get_or_create(
        name='41-27 Centennial',
        defaults={'group_type': 'home', 'description': 'Primary household group for 41-27 Centennial'}
    )
    for rm in Roommate.objects.filter(is_active=True):
        group.members.add(rm)

    het = Roommate.objects.filter(is_me=True).first()
    if het and not group.created_by:
        group.created_by = het
        group.save()

    Expense.objects.filter(group__isnull=True).update(group=group)
    Receipt.objects.filter(group__isnull=True).update(group=group)
    return group


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
        '.inprogress-payment-summary__total',
        '.order-dashboard-summary__total',
        '.order-summary-total-item--trimmed__estimated-total',
        '.preparing-order-summary .order-summary-sub-total__values',
    ]:
        el = soup.select_one(selector)
        if el:
            matches = re.findall(r'\d+\.\d{2}', el.get_text())
            if matches:
                return Decimal(matches[-1])

    # Text-based fallback search for 'Total'
    for tag in soup.find_all(lambda t: t.name in ['div', 'span', 'p'] and 'total' in t.get_text().lower()):
        txt = tag.get_text(strip=True)
        if re.search(r'Total', txt, re.I) and not re.search(r'subtotal', txt, re.I):
            m = re.findall(r'\$\s*(\d+\.\d{2})', txt)
            if m:
                return Decimal(m[-1])
    return None


def extract_items_from_soup(soup):
    """
    Extracts all line items with high precision:
    - Resolves accurate product titles
    - Handles input/numeric/weighted quantities
    - Extracts item total prices
    - Extracts CDN image URLs
    - Omits zero-dollar promotional stamps
    - Adds bottle deposit/environmental handling fee line item if order total > sum(items) + tax
    """
    parsed_items = []
    for li in soup.select('li.cart-entry-list__item'):
        # 1. Product Name
        name = None
        title_el = li.select_one(
            '.cart-entry__content--product-name, '
            '.item-details__item-name, '
            '[data-track="product-title"], '
            '.product-name, '
            '.cart-entry__content--product-details a, '
            '.cart-entry__content--product-details span'
        )
        if title_el and title_el.get_text(strip=True):
            name = title_el.get_text(strip=True)

        if not name:
            track_el = li.select_one('[data-track-products-array]')
            if track_el:
                try:
                    arr = json.loads(track_el['data-track-products-array'])
                    if arr and isinstance(arr, list) and arr[0].get('productName'):
                        name = arr[0]['productName'].strip()
                except Exception:
                    pass

        if not name:
            img_el = li.select_one('img[alt]')
            if img_el and img_el['alt'].strip():
                name = img_el['alt'].strip()

        name = name or 'Unknown Item'

        # 2. Quantity
        qty = None
        qty_input = li.select_one(
            'input.cart-entry__content__quantity, '
            'input[name*="quantity"], '
            'input[data-track*="quantity"]'
        )
        if qty_input and qty_input.get('value'):
            qty = qty_input['value'].strip()

        if not qty:
            qty_el = li.select_one(
                '.item-details__quantity, '
                '[data-track="product-qty"], '
                '.cart-entry__content--quantity'
            )
            if qty_el and qty_el.get_text(strip=True):
                qty = qty_el.get_text(strip=True)

        if not qty:
            track_el = li.select_one('[data-track-products-array]')
            if track_el:
                try:
                    arr = json.loads(track_el['data-track-products-array'])
                    if arr and isinstance(arr, list) and arr[0].get('productQuantity'):
                        qty = str(arr[0]['productQuantity'])
                except Exception:
                    pass

        qty = qty or '1'

        # 3. Price
        price_el = li.select_one(
            '.price--total .price__value, '
            '.cart-entry__content__price--total .price__value, '
            '.selling-price-list__item__price--now .price__value, '
            '.cart-entry__content--product-price .price__value, '
            '.price__value'
        )
        price = Decimal('0.00')
        if price_el:
            clean_str = re.sub(r'[^\d.]', '', price_el.get_text(strip=True))
            if clean_str:
                try:
                    price = Decimal(clean_str)
                except Exception:
                    pass

        # 4. Image
        img_el = li.select_one(
            'img.responsive-image, '
            'img.responsive-image__image, '
            '.cart-entry__content--image img, '
            'img'
        )
        image_url = None
        if img_el:
            image_url = img_el.get('src') or img_el.get('data-src')
            if image_url and image_url.startswith('//'):
                image_url = 'https:' + image_url

        # Exclude bash promotional stamps / non-grocery items
        if price == Decimal('0.00') and any(k in name.lower() for k in ['stamp', 'earn physical stamp', 'promo']):
            continue

        parsed_items.append({
            'name': name,
            'quantity_str': qty,
            'total_price': price,
            'image_url': image_url
        })

    # Check for bottle deposit / environmental handling fees / cart fee difference
    tax_val = parse_tax_from_soup(soup)
    order_total = parse_order_total_from_soup(soup)
    items_sum = sum((item['total_price'] for item in parsed_items), Decimal('0.00'))

    if order_total and order_total > (items_sum + tax_val):
        fee_diff = (order_total - (items_sum + tax_val)).quantize(Decimal('0.01'))
        if fee_diff > Decimal('0.00'):
            parsed_items.append({
                'name': 'Bottle Deposit & Environmental Fees',
                'quantity_str': '1',
                'total_price': fee_diff,
                'image_url': None
            })

    return parsed_items


def sync_receipts_folder(force_refresh=False):
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

        needs_reparse = force_refresh
        if receipt:
            if receipt.items.count() == 0 and len(soup.select('li.cart-entry-list__item')) > 0:
                needs_reparse = True
            elif receipt.items.filter(name='Unknown Item').exists():
                needs_reparse = True

        if not receipt:
            receipt = Receipt.objects.create(
                filename=fname,
                tax_amount=tax_val,
                order_date_str=order_date_str,
                file_modified_at=mtime
            )
            needs_reparse = True

        if needs_reparse:
            receipt.items.all().delete()
            parsed_items = extract_items_from_soup(soup)
            for it in parsed_items:
                ReceiptItem.objects.create(
                    receipt=receipt,
                    name=it['name'],
                    quantity_str=it['quantity_str'],
                    total_price=it['total_price'],
                    image_url=it['image_url']
                )
            if tax_val > Decimal('0.00'):
                receipt.tax_amount = tax_val
            if order_date_str:
                receipt.order_date_str = order_date_str
            receipt.file_modified_at = mtime
            receipt.save()
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
