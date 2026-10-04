import os
import sys
import shutil
from decimal import Decimal

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.environ['DJANGO_SETTINGS_MODULE'] = 'rcsscrapper.settings'

import django
django.setup()

from django.conf import settings
from django.db import connection
from django.core.management import call_command
from django.test import RequestFactory
from django.contrib.auth.models import User
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.messages.storage.fallback import FallbackStorage
from django.utils import timezone
from rcsscrapper.models import HouseholdGroup, Roommate, Expense, ExpenseSplit, Receipt, ReceiptItem, ItemShare
from rcsscrapper import views

# Point database to temporary demo database
demo_db_path = '/tmp/splitflow_synthetic_demo.sqlite3'
if os.path.exists(demo_db_path):
    os.remove(demo_db_path)

settings.DATABASES['default']['NAME'] = demo_db_path
connection.close()
call_command('migrate', verbosity=0)

# Seed synthetic users (Zero personal data)
alex_user = User.objects.create_superuser(
    username='alex',
    email='alex.chen@example.com',
    password='demopassword123',
    first_name='Alex Chen'
)
sarah_user = User.objects.create_user(
    username='sarah',
    email='sarah.j@example.com',
    password='demopassword123',
    first_name='Sarah Jenkins'
)
marcus_user = User.objects.create_user(
    username='marcus',
    email='marcus.v@example.com',
    password='demopassword123',
    first_name='Marcus Vance'
)
elena_user = User.objects.create_user(
    username='elena',
    email='elena.r@example.com',
    password='demopassword123',
    first_name='Elena Rostova'
)

# Seed Roommates
alex_rm = Roommate.objects.create(name='Alex Chen', user=alex_user, email=alex_user.email, is_me=True, is_active=True)
sarah_rm = Roommate.objects.create(name='Sarah Jenkins', user=sarah_user, email=sarah_user.email, is_me=False, is_active=True)
marcus_rm = Roommate.objects.create(name='Marcus Vance', user=marcus_user, email=marcus_user.email, is_me=False, is_active=True)
elena_rm = Roommate.objects.create(name='Elena Rostova', user=elena_user, email=elena_user.email, is_me=False, is_active=True)

all_rms = [alex_rm, sarah_rm, marcus_rm, elena_rm]

# Seed Groups
maple_group = HouseholdGroup.objects.create(
    name='The Maple Residence',
    group_type='home',
    description='Modern 4-bedroom apartment in Kitsilano',
    created_by=alex_rm
)
maple_group.members.add(*all_rms)

banff_group = HouseholdGroup.objects.create(
    name='Banff Weekend Getaway',
    group_type='trip',
    description='Cabin rental, ski passes, and shared roadtrip gas',
    created_by=marcus_rm
)
banff_group.members.add(alex_rm, marcus_rm, sarah_rm)

today = timezone.now().date()

# Seed Expenses & Splits for The Maple Residence
def add_split_expense(desc, amount, payer, cat, split_members, notes=""):
    exp = Expense.objects.create(
        group=maple_group,
        description=desc,
        amount=amount,
        paid_by=payer,
        category=cat,
        date=today,
        notes=notes,
        is_payment=False
    )
    per_person = (amount / len(split_members)).quantize(Decimal('0.01'))
    remainder = amount - (per_person * len(split_members))
    for idx, m in enumerate(split_members):
        share = per_person + (remainder if idx == 0 else Decimal('0.00'))
        ExpenseSplit.objects.create(expense=exp, roommate=m, amount=share)
    return exp

add_split_expense('Weekly Groceries (Superstore)', Decimal('164.50'), alex_rm, 'Groceries', all_rms, 'Fresh organic produce, milk, eggs, sourdough, pantry essentials')
add_split_expense('Fiber Gigabit Internet', Decimal('85.00'), marcus_rm, 'Utilities', all_rms, 'Monthly Telus PureFibre 1Gbps high speed connection')
add_split_expense('Hydro & Electricity Bill', Decimal('118.40'), sarah_rm, 'Utilities', all_rms, 'BC Hydro bimonthly residential electrical service')
add_split_expense('Eco Paper Towels & Cleaning Detergent', Decimal('28.75'), elena_rm, 'Household', all_rms, 'Kirkland paper towels, dishwashing pods, compost bags')
add_split_expense('Cold-Pressed Olive Oil & Spices', Decimal('34.20'), alex_rm, 'Groceries', [alex_rm, sarah_rm], 'Organic 1L cold-pressed olive oil, balsamic glaze, smoked sea salt')
add_split_expense('Living Room Modern Floor Lamp', Decimal('68.00'), marcus_rm, 'Household', all_rms, 'IKEA floor standing lamp with warm energy-saving LED bulb')

# Settle-Up Equalization Payment
settle_exp = Expense.objects.create(
    group=maple_group,
    description='Settle Up: Elena to Marcus',
    amount=Decimal('42.50'),
    paid_by=elena_rm,
    payment_to=marcus_rm,
    category='Payment',
    date=today,
    notes='Interac e-Transfer debt equalization settlement',
    is_payment=True
)
ExpenseSplit.objects.create(expense=settle_exp, roommate=marcus_rm, amount=Decimal('42.50'))

# Seed Pending Superstore Receipt
pending_receipt = Receipt.objects.create(
    group=maple_group,
    filename='superstore_order_84920412.html',
    order_date_str='October 3, 2026 at 4:30 PM',
    tax_amount=Decimal('2.15'),
    processed=False
)

receipt_items = [
    ('Lucerne 2% Partly Skimmed Milk 4L', '1', Decimal('5.99'), 'https://images.unsplash.com/photo-1550583724-b2692b85b150?w=120'),
    ('Large Brown Eggs Free Run 18pk', '1', Decimal('6.49'), 'https://images.unsplash.com/photo-1582722872445-44dc5f7e3c8f?w=120'),
    ('Organic Honeycrisp Apples 3lb Bag', '1', Decimal('7.99'), 'https://images.unsplash.com/photo-1560806887-1e4cd0b6cbd6?w=120'),
    ('Whole Grain Sliced Sandwich Bread', '1', Decimal('3.79'), 'https://images.unsplash.com/photo-1509440159596-0249088772ff?w=120'),
    ('PC Lemon Sparkling Water 12x355ml', '1', Decimal('6.49'), 'https://images.unsplash.com/photo-1622483767028-3f66f32aef97?w=120'),
    ('Extra Virgin Olive Oil Cold Pressed 1L', '1', Decimal('14.99'), 'https://images.unsplash.com/photo-1474979266404-7eaacbcd87c5?w=120'),
    ('Free From Boneless Skinless Chicken Breasts 1.2kg', '1', Decimal('18.50'), 'https://images.unsplash.com/photo-1604503468506-a8da13d82791?w=120'),
    ('Organic Baby Spinach Clamshell 312g', '1', Decimal('4.99'), 'https://images.unsplash.com/photo-1576045057995-568f588f82fb?w=120'),
    ('Medium Cheddar Cheese Block 400g', '1', Decimal('6.99'), 'https://images.unsplash.com/photo-1618160702438-9b02ab6515c9?w=120'),
    ('Bottle Deposit & Environmental Fees', '1', Decimal('1.45'), None),
]

for name, qty, price, img in receipt_items:
    ReceiptItem.objects.create(
        receipt=pending_receipt,
        name=name,
        quantity_str=qty,
        total_price=price,
        image_url=img,
        is_assigned=False
    )

# Seed Processed Receipt with Assigned Shares
processed_receipt = Receipt.objects.create(
    group=maple_group,
    filename='superstore_order_83901928.html',
    order_date_str='September 28, 2026 at 2:15 PM',
    tax_amount=Decimal('1.80'),
    processed=True
)
for name, qty, price, img in receipt_items[:4]:
    item = ReceiptItem.objects.create(
        receipt=processed_receipt,
        name=name,
        quantity_str=qty,
        total_price=price,
        image_url=img,
        is_assigned=True
    )
    for rm in all_rms:
        ItemShare.objects.create(item=item, roommate=rm, units=Decimal('1.00'))

# Render View Pipelines using Django RequestFactory
factory = RequestFactory()

def build_request(path):
    req = factory.get(path)
    req.user = alex_user
    req.session = SessionStore()
    req.session['active_group_id'] = maple_group.id
    req.session.save()
    setattr(req, '_messages', FallbackStorage(req))
    return req

demo_banner = """
<!-- SplitFlow Interactive Demo Banner -->
<aside aria-label="Demo Environment Notice" class="bg-gradient-to-r from-emerald-600 to-teal-700 text-white text-xs py-2.5 px-4 shadow-sm flex flex-wrap items-center justify-between gap-3 border-b border-emerald-500/30">
  <div class="flex items-center gap-2.5 font-medium">
    <span class="relative flex h-2.5 w-2.5">
      <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-300 opacity-75"></span>
      <span class="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-200"></span>
    </span>
    <span><strong>SplitFlow Live Demo</strong> &bull; Production Code &amp; Templates &bull; Synthetic Demo: <em>The Maple Residence</em></span>
  </div>
  <div class="flex items-center gap-4 text-xs font-semibold">
    <span class="hidden sm:inline text-emerald-100/90">Zero Personal Data &bull; Fully Interactive</span>
    <a href="https://github.com/D-ENCODER/SplitFlow" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1.5 bg-white/10 hover:bg-white/20 text-white px-2.5 py-1 rounded transition border border-white/20">
      <span>View GitHub Repo</span>
      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"/></svg>
    </a>
  </div>
</aside>
"""

modal_demo_interactivity = """
<script>
  // SplitFlow Demo Interactivity Shim for Static Pages
  document.addEventListener('DOMContentLoaded', function() {
    // Intercept Add Expense modal form submit
    const expForm = document.querySelector('#expense-modal form');
    if (expForm) {
      expForm.addEventListener('submit', function(e) {
        e.preventDefault();
        alert('Expense recorded in demo mode! In a live deployment, this transaction writes atomically to the database.');
        if (typeof closeExpenseModal === 'function') closeExpenseModal();
      });
    }

    // Intercept Settle Up modal form submit
    const settleForm = document.querySelector('#settle-modal form');
    if (settleForm) {
      settleForm.addEventListener('submit', function(e) {
        e.preventDefault();
        alert('Equalization payment recorded in demo mode!');
        if (typeof closeSettleModal === 'function') closeSettleModal();
      });
    }

    // Ensure links to Django routes gracefully navigate static demo pages
    document.querySelectorAll('a[href]').forEach(function(link) {
      const href = link.getAttribute('href');
      if (href === '/') link.setAttribute('href', 'index.html');
      else if (href === '/groups/') link.setAttribute('href', 'groups.html');
      else if (href === '/analytics/') link.setAttribute('href', 'analytics.html');
      else if (href === '/receipts/') link.setAttribute('href', 'receipts.html');
      else if (href === '/groups/import-splitwise/') link.setAttribute('href', 'import_splitwise.html');
      else if (href.startsWith('/batch/assign/')) link.setAttribute('href', 'batch_process.html');
      else if (href.startsWith('/batch/summary/')) link.setAttribute('href', 'batch_summary.html');
    });
  });
</script>
"""

def post_process_html(html_content, current_page):
    # Insert demo banner right after <body>
    body_idx = html_content.find('<body')
    if body_idx != -1:
        end_body_open = html_content.find('>', body_idx)
        if end_body_open != -1:
            html_content = html_content[:end_body_open+1] + '\n' + demo_banner + '\n' + html_content[end_body_open+1:]

    # Append interactive JS before </body>
    html_content = html_content.replace('</body>', modal_demo_interactivity + '\n</body>')

    # Replace relative route URLs with static html file links
    url_mappings = [
        ('href="/"', 'href="index.html"'),
        ('href="/groups/"', 'href="groups.html"'),
        ('href="/analytics/"', 'href="analytics.html"'),
        ('href="/receipts/"', 'href="receipts.html"'),
        ('href="/groups/import-splitwise/"', 'href="import_splitwise.html"'),
        (f'href="/batch/assign/?ids={pending_receipt.id}"', 'href="batch_process.html"'),
        (f'href="/batch/summary/?ids={processed_receipt.id}"', 'href="batch_summary.html"'),
        ('href="/batch/assign/"', 'href="batch_process.html"'),
        ('href="/batch/summary/"', 'href="batch_summary.html"'),
        ('href="/manifest.json"', 'href="static/manifest.json"'),
        ('href="/profile/"', 'href="#"'),
        ('href="/login/"', 'href="#"'),
        ('href="/logout/"', 'href="#"'),
        ('href="/register/"', 'href="#"'),
        ('href="/groups/create/"', 'href="#"'),
        ('src="/static/', 'src="static/'),
        ('href="/static/', 'href="static/'),
    ]
    for old, new in url_mappings:
        html_content = html_content.replace(old, new)

    return html_content

# 1. Render Dashboard
req_dash = build_request('/')
resp_dash = views.splitwise_dashboard(req_dash)
html_dash = post_process_html(resp_dash.content.decode('utf-8'), 'index.html')
with open('docs/index.html', 'w', encoding='utf-8') as f:
    f.write(html_dash)

# 2. Render Groups
req_groups = build_request('/groups/')
resp_groups = views.group_list(req_groups)
html_groups = post_process_html(resp_groups.content.decode('utf-8'), 'groups.html')
with open('docs/groups.html', 'w', encoding='utf-8') as f:
    f.write(html_groups)

# 3. Render Analytics
req_analytics = build_request('/analytics/')
resp_analytics = views.analytics_dashboard(req_analytics)
html_analytics = post_process_html(resp_analytics.content.decode('utf-8'), 'analytics.html')
with open('docs/analytics.html', 'w', encoding='utf-8') as f:
    f.write(html_analytics)

# 4. Render Receipts Inbox
req_receipts = build_request('/receipts/')
resp_receipts = views.receipt_list(req_receipts)
html_receipts = post_process_html(resp_receipts.content.decode('utf-8'), 'receipts.html')
with open('docs/receipts.html', 'w', encoding='utf-8') as f:
    f.write(html_receipts)

# 5. Render Import Splitwise
req_import = build_request('/groups/import-splitwise/')
resp_import = views.import_splitwise_view(req_import)
html_import = post_process_html(resp_import.content.decode('utf-8'), 'import_splitwise.html')
with open('docs/import_splitwise.html', 'w', encoding='utf-8') as f:
    f.write(html_import)

# 6. Render Batch Process (Receipt Item Assignment)
req_batch = build_request(f'/batch/assign/?ids={pending_receipt.id}')
resp_batch = views.batch_process(req_batch)
if hasattr(resp_batch, 'content'):
    html_batch = post_process_html(resp_batch.content.decode('utf-8'), 'batch_process.html')
    with open('docs/batch_process.html', 'w', encoding='utf-8') as f:
        f.write(html_batch)

# 7. Render Batch Summary
req_summary = build_request(f'/batch/summary/?ids={processed_receipt.id}')
resp_summary = views.batch_summary(req_summary)
if hasattr(resp_summary, 'content'):
    html_summary = post_process_html(resp_summary.content.decode('utf-8'), 'batch_summary.html')
    with open('docs/batch_summary.html', 'w', encoding='utf-8') as f:
        f.write(html_summary)

# 8. Render 404 Fallback for GitHub Pages
from django.shortcuts import render
req_404 = build_request('/404/')
resp_404 = render(req_404, '404.html', {'is_admin': True})
html_404 = post_process_html(resp_404.content.decode('utf-8'), '404.html')
with open('docs/404.html', 'w', encoding='utf-8') as f:
    f.write(html_404)

print("Successfully generated all production-exact HTML demo pages in docs/!")
