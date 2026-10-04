import os
import subprocess
import tempfile
from decimal import Decimal
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.contrib import messages
from django.utils import timezone
from collections import defaultdict
from django.core.paginator import Paginator
import json
import re
from bs4 import BeautifulSoup

from rcsscrapper.models import Receipt, ReceiptItem, Roommate, ItemShare, Expense, ExpenseSplit, HouseholdGroup
from rcsscrapper.utils import sync_receipts_folder, ensure_roommates, INBOX_DIR
from rcsscrapper.services import sync_receipt_to_expense, sync_all_receipts, calculate_balances, import_and_clean_splitwise_csv
from rcsscrapper.context_processors import get_active_group

from functools import wraps

def is_admin_user(user):
    return bool(user and user.is_authenticated and (user.is_superuser or user.is_staff or user.username == 'admin'))


def admin_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('login')}?next={request.path}")
        if not is_admin_user(request.user):
            messages.error(request, "Permission denied: The Superstore Receipt Inbox is restricted to administrators.")
            return redirect('splitwise_dashboard')
        return view_func(request, *args, **kwargs)
    return _wrapped_view



def get_active_roommate(request):
    """
    Returns the Roommate associated with the logged-in user.
    If the logged-in user is admin without an active roommate split account,
    returns the primary household account (Het).
    """
    ensure_roommates()
    if request.user.is_authenticated:
        if hasattr(request.user, 'roommate') and request.user.roommate and request.user.roommate.is_active:
            return request.user.roommate
        # Admin or staff fallback
        het = Roommate.objects.filter(is_me=True, is_active=True).first()
        if het:
            return het
    return Roommate.objects.filter(is_me=True).first() or Roommate.objects.first()


# ==========================================
# AUTHENTICATION & PROFILE VIEWS
# ==========================================

def login_view(request):
    ensure_roommates()
    error_msg = None
    next_url = request.GET.get('next') or request.POST.get('next') or reverse('splitwise_dashboard')

    if request.user.is_authenticated:
        return redirect(next_url)

    if request.method == 'POST':
        username_or_email = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')

        user = authenticate(request, username=username_or_email, password=password)
        if not user:
            user_obj = User.objects.filter(email__iexact=username_or_email).first()
            if user_obj:
                user = authenticate(request, username=user_obj.username, password=password)

        if user:
            login(request, user)
            rm_name = user.roommate.name if hasattr(user, 'roommate') and user.roommate else user.username
            messages.success(request, f"Welcome back, {rm_name}!")
            return redirect(next_url)
        else:
            error_msg = "Invalid username/email or password. Please try again."

    return render(request, 'splitter/login.html', {
        'error_msg': error_msg,
        'next': next_url,
    })


def register_view(request):
    ensure_roommates()
    error_msg = None

    if request.user.is_authenticated:
        return redirect('splitwise_dashboard')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip().lower()
        full_name = request.POST.get('full_name', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        password_confirm = request.POST.get('password_confirm', '')

        if not username or not full_name:
            error_msg = "Username and Full Name are required."
        elif User.objects.filter(username=username).exists():
            error_msg = f"Username '{username}' is already taken."
        elif email and User.objects.filter(email=email).exists():
            error_msg = f"Email '{email}' is already registered."
        elif password != password_confirm:
            error_msg = "Passwords do not match."
        elif len(password) < 4:
            error_msg = "Password must be at least 4 characters."
        else:
            user = User.objects.create_user(
                username=username,
                email=email or f"{username}@splitflow.local",
                password=password,
                first_name=full_name
            )
            # Create or link active Roommate profile
            rm, _ = Roommate.objects.get_or_create(
                name=full_name,
                defaults={'user': user, 'email': user.email, 'is_me': False, 'is_active': True}
            )
            rm.user = user
            rm.email = user.email
            rm.is_active = True
            rm.save()

            login(request, user)
            messages.success(request, f"Welcome to SplitFlow, {full_name}! Your profile is ready.")
            return redirect('splitwise_dashboard')

    return render(request, 'splitter/register.html', {
        'error_msg': error_msg,
    })


def logout_view(request):
    logout(request)
    messages.info(request, "You have been logged out.")
    return redirect('login')


def forgot_password_view(request):
    ensure_roommates()
    message = None
    error_msg = None

    if request.method == 'POST':
        identifier = request.POST.get('identifier', '').strip()
        new_password = request.POST.get('new_password', '')
        confirm_password = request.POST.get('confirm_password', '')

        user = User.objects.filter(username__iexact=identifier).first() or \
               User.objects.filter(email__iexact=identifier).first()

        if not user:
            error_msg = f"No profile found matching '{identifier}'."
        elif not new_password:
            error_msg = "Please enter a new password."
        elif new_password != confirm_password:
            error_msg = "Passwords do not match."
        elif len(new_password) < 4:
            error_msg = "Password must be at least 4 characters."
        else:
            user.set_password(new_password)
            user.save()
            messages.success(request, f"Password successfully updated for {user.username}! You can now log in.")
            return redirect('login')

    return render(request, 'splitter/forgot_password.html', {
        'message': message,
        'error_msg': error_msg,
    })


@login_required(login_url='login')
def profile_view(request):
    """
    Dedicated Profile view:
    - User details (name, username, email, member since)
    - Role badge (Administrator vs Roommate)
    - Personal Ledger standing & debt breakdown
    - Profile update and password change actions
    - Admin-only management tools (roommate roster, status toggle, password reset, stats)
    """
    ensure_roommates()
    user = request.user
    is_admin = is_admin_user(user)
    user_rm = getattr(user, 'roommate', None)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'update_profile':
            full_name = request.POST.get('full_name', '').strip()
            email = request.POST.get('email', '').strip()

            if not full_name:
                messages.error(request, "Full name cannot be blank.")
            else:
                user.first_name = full_name
                if email:
                    user.email = email
                user.save()

                if user_rm:
                    user_rm.name = full_name
                    if email:
                        user_rm.email = email
                    user_rm.save()
                messages.success(request, "Profile details updated successfully!")
            return redirect('profile')

        elif action == 'change_password':
            current_pwd = request.POST.get('current_password', '')
            new_pwd = request.POST.get('new_password', '')
            confirm_pwd = request.POST.get('confirm_password', '')

            if not user.check_password(current_pwd):
                messages.error(request, "Current password is incorrect.")
            elif not new_pwd or len(new_pwd) < 4:
                messages.error(request, "New password must be at least 4 characters.")
            elif new_pwd != confirm_pwd:
                messages.error(request, "New passwords do not match.")
            else:
                user.set_password(new_pwd)
                user.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Your password has been changed successfully!")
            return redirect('profile')

        elif action == 'toggle_roommate_status' and is_admin:
            rm_id = request.POST.get('roommate_id')
            target_rm = get_object_or_404(Roommate, id=rm_id)
            target_rm.is_active = not target_rm.is_active
            target_rm.save()
            messages.success(request, f"Roommate {target_rm.name} is now {'Active' if target_rm.is_active else 'Inactive'}.")
            return redirect('profile')

        elif action == 'reset_roommate_password' and is_admin:
            target_user_id = request.POST.get('user_id')
            new_pwd = request.POST.get('new_password', '').strip() or 'admin'
            target_user = get_object_or_404(User, id=target_user_id)
            target_user.set_password(new_pwd)
            target_user.save()
            messages.success(request, f"Password for {target_user.username} was reset to '{new_pwd}'.")
            return redirect('profile')

    # Personal finance stats if linked to an active roommate
    personal_summary = None
    total_paid = Decimal('0.00')
    total_share = Decimal('0.00')

    if user_rm and user_rm.is_active:
        ledger_data = calculate_balances(current_roommate=user_rm)
        personal_summary = ledger_data['user_summary']

        total_paid = sum(
            Expense.objects.filter(paid_by=user_rm, is_payment=False).values_list('amount', flat=True)
        )
        total_share = sum(
            ExpenseSplit.objects.filter(roommate=user_rm, expense__is_payment=False).values_list('amount', flat=True)
        )

    # Admin data
    all_roommates_admin = []
    system_stats = {}
    if is_admin:
        all_roommates_admin = list(Roommate.objects.all().select_related('user').order_by('-is_active', 'name'))
        total_exp_count = Expense.objects.count()
        total_exp_spent = sum(Expense.objects.filter(is_payment=False).values_list('amount', flat=True))
        total_receipts_count = Receipt.objects.count()
        system_stats = {
            'total_exp_count': total_exp_count,
            'total_exp_spent': total_exp_spent,
            'total_receipts_count': total_receipts_count,
            'total_users_count': User.objects.count(),
            'tailscale_url': 'https://sosuke-aizen.warg-rainbow.ts.net:8443',
        }

    return render(request, 'splitter/profile.html', {
        'profile_user': user,
        'user_rm': user_rm,
        'is_admin': is_admin,
        'personal_summary': personal_summary,
        'total_paid': total_paid,
        'total_share': total_share,
        'all_roommates_admin': all_roommates_admin,
        'system_stats': system_stats,
    })


# ==========================================
# SPLITWISE CORE EXPENSES & LEDGER VIEWS
# ==========================================

@login_required(login_url='login')
def splitwise_dashboard(request):
    """
    Main Splitwise dashboard:
    - User is strictly locked to their own account perspective
    - Net household balance summary
    - Friend-by-friend debt breakdown
    - Chronological shared expenses & settlements feed
    """
    sync_receipts_folder()
    sync_all_receipts()
    active_roommate = get_active_roommate(request)
    is_admin = is_admin_user(request.user)
    active_group = get_active_group(request)

    ledger_data = calculate_balances(current_roommate=active_roommate, group=active_group)
    all_roommates = ledger_data['all_roommates']
    user_summary = ledger_data['user_summary']
    expenses = ledger_data['expenses']

    # Annotate viewer-specific relation to each expense
    annotated_expenses = []
    for exp in expenses:
        viewer_is_payer = (exp.paid_by_id == active_roommate.id)
        viewer_split = next((s for s in exp.splits.all() if s.roommate_id == active_roommate.id), None)
        viewer_share = viewer_split.amount if viewer_split else Decimal('0.00')

        if exp.is_payment:
            # Settlement record
            if viewer_is_payer:
                impact = f"you paid ${exp.amount}"
                impact_type = 'paid'
            elif exp.payment_to_id == active_roommate.id:
                impact = f"paid you ${exp.amount}"
                impact_type = 'received'
            else:
                impact = "not involved"
                impact_type = 'none'
        else:
            # Regular shared expense
            if viewer_is_payer:
                net_lent = exp.amount - viewer_share
                if net_lent > Decimal('0.00'):
                    impact = f"you lent ${net_lent}"
                    impact_type = 'lent'
                else:
                    impact = "you paid for yourself"
                    impact_type = 'neutral'
            else:
                if viewer_share > Decimal('0.00'):
                    impact = f"you owe ${viewer_share}"
                    impact_type = 'borrowed'
                else:
                    impact = "not involved"
                    impact_type = 'none'

        annotated_expenses.append({
            'expense': exp,
            'impact': impact,
            'impact_type': impact_type,
            'viewer_share': viewer_share,
            'can_delete': is_admin or (exp.paid_by_id == active_roommate.id),
        })

    inbox_receipts_count = Receipt.objects.filter(is_archived=False).count()
    pending_receipts_count = Receipt.objects.filter(is_archived=False, processed=False).count()

    search_q = request.GET.get('q', '').strip()
    selected_cat = request.GET.get('category', '').strip()

    if search_q:
        q_lower = search_q.lower()
        annotated_expenses = [
            item for item in annotated_expenses
            if q_lower in item['expense'].description.lower() or
               q_lower in item['expense'].notes.lower() or
               q_lower in item['expense'].paid_by.name.lower() or
               (item['expense'].payment_to and q_lower in item['expense'].payment_to.name.lower())
        ]

    if selected_cat:
        annotated_expenses = [
            item for item in annotated_expenses
            if item['expense'].category.lower() == selected_cat.lower()
        ]

    paginator = Paginator(annotated_expenses, 35)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    return render(request, 'splitter/dashboard.html', {
        'active_roommate': active_roommate,
        'user_summary': user_summary,
        'all_roommates': all_roommates,
        'active_group': active_group,
        'page_obj': page_obj,
        'total_expenses_count': len(annotated_expenses),
        'search_q': search_q,
        'selected_cat': selected_cat,
        'inbox_receipts_count': inbox_receipts_count,
        'pending_receipts_count': pending_receipts_count,
        'category_choices': Expense.CATEGORY_CHOICES,
        'is_admin': is_admin,
    })


@login_required(login_url='login')
def add_expense(request):
    """Create a manual shared expense split equally or with exact custom amounts within active group."""
    active_roommate = get_active_roommate(request)
    active_group = get_active_group(request)
    is_admin = is_admin_user(request.user)

    if request.method == 'POST':
        group_id = request.POST.get('group_id')
        target_group = HouseholdGroup.objects.filter(id=group_id).first() if group_id else active_group
        group_members = list(target_group.members.filter(is_active=True)) if target_group else []
        roommates = group_members if group_members else list(Roommate.objects.filter(is_active=True))

        desc = request.POST.get('description', '').strip()
        amount_raw = request.POST.get('amount', '0').strip().replace('$', '')
        category = request.POST.get('category', 'General')
        notes = request.POST.get('notes', '').strip()
        date_str = request.POST.get('date')

        # Non-admin users can only record expenses paid by themselves
        if is_admin:
            paid_by_id = request.POST.get('paid_by')
            payer = Roommate.objects.filter(id=paid_by_id).first() or active_roommate
        else:
            payer = active_roommate

        try:
            total_amount = Decimal(amount_raw)
        except Exception:
            messages.error(request, "Please enter a valid expense amount.")
            return redirect('splitwise_dashboard')

        if total_amount <= Decimal('0.00'):
            messages.error(request, "Expense amount must be greater than $0.")
            return redirect('splitwise_dashboard')

        exp_date = timezone.now().date()
        if date_str:
            try:
                exp_date = timezone.datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                pass

        split_type = request.POST.get('split_type', 'equal')
        selected_rms = request.POST.getlist('split_roommates')

        splits_to_create = {}
        if split_type == 'equal':
            target_rms = [r for r in roommates if str(r.id) in selected_rms] if selected_rms else roommates
            if not target_rms:
                target_rms = roommates
            n = len(target_rms)
            base_share = (total_amount / Decimal(n)).quantize(Decimal('0.01'))
            splits_to_create = {r: base_share for r in target_rms}
            diff = total_amount - sum(splits_to_create.values())
            first_rm = next((r for r in target_rms if r == payer), target_rms[0])
            splits_to_create[first_rm] += diff
        else:
            for r in roommates:
                val = request.POST.get(f'exact_amount_{r.id}', '0').strip().replace('$', '')
                try:
                    amt = Decimal(val or '0')
                    if amt > Decimal('0.00'):
                        splits_to_create[r] = amt
                except Exception:
                    pass

        if not splits_to_create:
            messages.error(request, "No roommates selected to split this expense.")
            return redirect('splitwise_dashboard')

        expense = Expense.objects.create(
            group=target_group,
            description=desc or "Shared Expense",
            amount=total_amount,
            category=category,
            paid_by=payer,
            date=exp_date,
            notes=notes,
            is_payment=False
        )

        for r, amt in splits_to_create.items():
            ExpenseSplit.objects.create(
                expense=expense,
                roommate=r,
                amount=amt
            )

        messages.success(request, f"Added '{expense.description}' for ${total_amount}!")
        return redirect('splitwise_dashboard')

    return redirect('splitwise_dashboard')


@login_required(login_url='login')
def settle_up(request):
    """Record a debt settlement payment between two roommates in active group."""
    active_roommate = get_active_roommate(request)
    active_group = get_active_group(request)
    is_admin = is_admin_user(request.user)

    if request.method == 'POST':
        group_id = request.POST.get('group_id')
        target_group = HouseholdGroup.objects.filter(id=group_id).first() if group_id else active_group
        payer_id = request.POST.get('payer_id')
        recipient_id = request.POST.get('recipient_id')
        amount_raw = request.POST.get('amount', '0').strip().replace('$', '')
        notes = request.POST.get('notes', '').strip()
        date_str = request.POST.get('date')

        try:
            amount = Decimal(amount_raw)
        except Exception:
            messages.error(request, "Please enter a valid settlement amount.")
            return redirect('splitwise_dashboard')

        if amount <= Decimal('0.00'):
            messages.error(request, "Payment amount must be greater than $0.")
            return redirect('splitwise_dashboard')

        if payer_id == recipient_id:
            messages.error(request, "Payer and recipient cannot be the same person.")
            return redirect('splitwise_dashboard')

        payer = get_object_or_404(Roommate, id=payer_id)
        recipient = get_object_or_404(Roommate, id=recipient_id)

        # Non-admin roommates must be a party in the payment
        if not is_admin and active_roommate.id not in [payer.id, recipient.id]:
            messages.error(request, "Permission denied: You must be either the payer or the recipient in this settlement.")
            return redirect('splitwise_dashboard')

        pay_date = timezone.now().date()
        if date_str:
            try:
                pay_date = timezone.datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                pass

        Expense.objects.create(
            group=target_group,
            description=f"Payment from {payer.name} to {recipient.name}",
            amount=amount,
            category='General',
            paid_by=payer,
            payment_to=recipient,
            date=pay_date,
            notes=notes or "Settled via SplitFlow",
            is_payment=True
        )

        messages.success(request, f"Recorded payment: {payer.name} paid {recipient.name} ${amount}!")
        return redirect('splitwise_dashboard')

    return redirect('splitwise_dashboard')


@login_required(login_url='login')
def delete_expense(request, expense_id):
    """Delete a shared expense or settlement payment (permission guarded)."""
    expense = Expense.objects.filter(id=expense_id).first()
    if not expense:
        messages.info(request, "Expense was already deleted or not found.")
        return redirect('splitwise_dashboard')
    active_roommate = get_active_roommate(request)
    is_admin = is_admin_user(request.user)

    # Guard: only the person who paid or an administrator can delete
    if not is_admin and expense.paid_by_id != active_roommate.id:
        messages.error(request, "Permission denied: You can only delete expenses that you paid for.")
        return redirect('splitwise_dashboard')

    desc = expense.description
    amt = expense.amount

    if expense.receipt:
        expense.receipt = None
        expense.save()

    expense.delete()
    messages.info(request, f"Deleted '{desc}' (${amt}).")
    return redirect('splitwise_dashboard')


# ==========================================
# SUPERSTORE INBOX & SPLITTER VIEWS
# ==========================================

@csrf_exempt
def api_ingest_order(request):
    if request.method == "POST":
        data = json.loads(request.body)
        html_content = data.get("html", "")

        match = re.search(r"Order\s*#(\d+)", html_content)
        order_id = match.group(1) if match else "latest"
        filename = f"Superstore_Order_{order_id}.html"

        soup = BeautifulSoup(html_content, "html.parser")
        wrappers = soup.select("div.single-column-wrapper")
        if wrappers:
            combined = "\n".join(str(w) for w in wrappers)
            if "cart-entry-list__item" in combined:
                html_content = combined

        os.makedirs(INBOX_DIR, exist_ok=True)
        filepath = os.path.join(INBOX_DIR, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html_content)

        existing = Receipt.objects.filter(filename=filename).first()
        if existing:
            if existing.items.count() == 0:
                existing.delete()
            else:
                existing.is_archived = False
                existing.save()

        sync_receipts_folder()

        rec = Receipt.objects.filter(filename=filename).first()
        item_count = rec.items.count() if rec else 0

        response = JsonResponse({
            "status": "ok",
            "filename": filename,
            "items_found": item_count
        })
        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Headers"] = "Content-Type"
        return response

    response = HttpResponse()
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response["Access-Control-Allow-Headers"] = "Content-Type"
    return response


def delete_receipts_and_files(queryset):
    for rec in queryset:
        filepath = os.path.join(INBOX_DIR, rec.filename)
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                pass
        Expense.objects.filter(receipt=rec).delete()
    queryset.delete()


@admin_required
def delete_receipt(request, receipt_id):
    receipt = Receipt.objects.filter(id=receipt_id).first()
    if receipt:
        was_archived = receipt.is_archived
        fname = receipt.filename
        delete_receipts_and_files(Receipt.objects.filter(id=receipt.id))
        messages.success(request, f"Receipt '{fname}' deleted successfully.")
        if was_archived:
            return redirect(f"{reverse('receipt_list')}?archived=1")
    else:
        messages.info(request, "Receipt was already deleted or not found.")
    return redirect('receipt_list')


def latex_escape(text):
    conv = {
        '&': r'\&', '%': r'\%', '$': r'\$', '#': r'\#',
        '_': r'\_', '{': r'\{', '}': r'\}', '~': r'\textasciitilde{}',
        '^': r'\^{}', '\\': r'\textbackslash{}',
    }
    return ''.join(conv.get(c, c) for c in str(text))


@admin_required
def receipt_list(request):
    sync_receipts_folder()
    show_archived = request.GET.get('archived') == '1'

    if request.method == 'POST':
        selected_ids = request.POST.getlist('receipt_ids')
        action = request.POST.get('action', 'process')
        if selected_ids:
            ids_str = ','.join(selected_ids)
            if action == 'archive':
                Receipt.objects.filter(id__in=selected_ids).update(is_archived=True)
                return redirect('receipt_list')
            elif action == 'unarchive':
                Receipt.objects.filter(id__in=selected_ids).update(is_archived=False)
                return redirect(f"{reverse('receipt_list')}?archived=1")
            elif action == 'delete':
                delete_receipts_and_files(Receipt.objects.filter(id__in=selected_ids))
                if show_archived:
                    return redirect(f"{reverse('receipt_list')}?archived=1")
                return redirect('receipt_list')
            elif action == 'reprocess':
                ReceiptItem.objects.filter(receipt_id__in=selected_ids).delete()
                Receipt.objects.filter(id__in=selected_ids).update(processed=False)
                Expense.objects.filter(receipt_id__in=selected_ids).delete()
                sync_receipts_folder(force_refresh=True)
                return redirect(f"{reverse('batch_process')}?ids={ids_str}")
            elif action == 'summary':
                return redirect(f"{reverse('batch_summary')}?ids={ids_str}")
            return redirect(f"{reverse('batch_process')}?ids={ids_str}")

    receipts = Receipt.objects.filter(is_archived=show_archived).order_by('-file_modified_at', '-created_at')
    archived_count = Receipt.objects.filter(is_archived=True).count()

    return render(request, 'splitter/receipt_list.html', {
        'receipts': receipts,
        'show_archived': show_archived,
        'archived_count': archived_count,
    })


@admin_required
def toggle_archive_receipt(request, receipt_id):
    receipt = Receipt.objects.filter(id=receipt_id).first()
    if receipt:
        receipt.is_archived = not receipt.is_archived
        receipt.save()
        status_str = "Archived" if receipt.is_archived else "Restored"
        messages.success(request, f"Receipt '{receipt.filename}' is now {status_str}.")
    else:
        messages.info(request, "Receipt not found or already removed.")
    return redirect('receipt_list')


@admin_required
def batch_process(request):
    ensure_roommates()
    ids_param = request.GET.get('ids', '')
    receipt_ids = [int(x) for x in ids_param.split(',') if x.isdigit()]
    if not receipt_ids:
        return redirect('receipt_list')

    roommates = list(Roommate.objects.filter(is_active=True))
    all_items = list(
        ReceiptItem.objects.filter(receipt_id__in=receipt_ids)
        .select_related('receipt')
        .prefetch_related('shares')
        .order_by('receipt__file_modified_at', 'id')
    )

    specific_item_id = request.GET.get('item_id')
    return_to = request.GET.get('from', '')

    item = None
    if specific_item_id and specific_item_id.isdigit():
        item = next((i for i in all_items if i.id == int(specific_item_id)), None)

    if not item:
        item = next((i for i in all_items if not i.is_assigned), None)

    if not item:
        Receipt.objects.filter(id__in=receipt_ids).update(processed=True)
        for r_id in receipt_ids:
            r_obj = Receipt.objects.filter(id=r_id).first()
            if r_obj:
                sync_receipt_to_expense(r_obj)
        return redirect(f"{reverse('batch_summary')}?ids={ids_param}")

    if request.method == 'POST':
        ItemShare.objects.filter(item=item).delete()
        total_units = Decimal('0')

        for r in roommates:
            units = Decimal(request.POST.get(f'units_{r.id}', '0') or '0')
            if units > 0:
                ItemShare.objects.create(item=item, roommate=r, units=units)
                total_units += units

        if total_units > 0:
            item.is_assigned = True
            item.save()
            if not item.receipt.items.filter(is_assigned=False).exists():
                item.receipt.processed = True
                item.receipt.save()
                sync_receipt_to_expense(item.receipt)

            if return_to == 'summary':
                return redirect(f"{reverse('batch_summary')}?ids={ids_param}")
            return redirect(f"{reverse('batch_process')}?ids={ids_param}")

    current_idx = all_items.index(item)
    prev_item = all_items[current_idx - 1] if current_idx > 0 else None

    num_roommates = len(roommates)
    batch_receipts = Receipt.objects.filter(id__in=receipt_ids)
    total_batch_tax = sum((r.tax_amount for r in batch_receipts), Decimal('0.00'))
    tax_per_person = (total_batch_tax / Decimal(num_roommates)) if num_roommates > 0 else Decimal('0.00')

    base_totals = {r.id: tax_per_person for r in roommates}
    for other_item in all_items:
        if other_item.id == item.id or not other_item.is_assigned:
            continue
        shares = list(other_item.shares.all())
        tot_u = sum(s.units for s in shares)
        if tot_u > 0:
            for s in shares:
                base_totals[s.roommate_id] += other_item.total_price * (s.units / tot_u)

    initial_units = {r.id: Decimal('0') for r in roommates}
    smart_matched = False

    existing_shares = list(item.shares.all())
    if existing_shares:
        for s in existing_shares:
            initial_units[s.roommate_id] = s.units
    else:
        last_assigned = (
            ReceiptItem.objects.filter(name__iexact=item.name, is_assigned=True)
            .exclude(id=item.id)
            .order_by('-id')
            .first()
        )
        if last_assigned:
            for s in last_assigned.shares.all():
                initial_units[s.roommate_id] = s.units
            smart_matched = True

    roommate_rows = [
        {
            'roommate': r,
            'initial_unit': f"{initial_units[r.id]:g}",
            'base_total': f"{base_totals[r.id]:.4f}",
        }
        for r in roommates
    ]

    return render(request, 'splitter/assign_item.html', {
        'receipt': item.receipt,
        'item': item,
        'roommates': roommates,
        'roommate_rows': roommate_rows,
        'smart_matched': smart_matched,
        'prev_item': prev_item,
        'return_to': return_to,
        'ids_param': ids_param,
        'progress': {
            'current': current_idx + 1,
            'total': len(all_items),
            'receipt_count': len(receipt_ids),
        }
    })


def build_summary_data(receipt_ids):
    sync_receipts_folder()
    receipts = Receipt.objects.filter(id__in=receipt_ids).order_by('file_modified_at', 'id')
    roommates = list(Roommate.objects.filter(is_active=True))
    num_roommates = len(roommates)

    overall_items = {r: Decimal('0.00') for r in roommates}
    overall_tax = {r: Decimal('0.00') for r in roommates}
    receipt_reports = []

    for receipt in receipts:
        r_items_exact = {r: Decimal('0.0000') for r in roommates}
        item_rows = []
        actual_items_sum = Decimal('0.00')

        for item in receipt.items.prefetch_related('shares__roommate'):
            actual_items_sum += item.total_price
            total_units = sum(s.units for s in item.shares.all())
            splits_dict = {r.name: Decimal('0.00') for r in roommates}
            splits_str = []

            if total_units > 0:
                for share in item.shares.all():
                    exact_cost = item.total_price * (share.units / total_units)
                    r_items_exact[share.roommate] += exact_cost
                    display_cost = exact_cost.quantize(Decimal('0.01'))
                    splits_dict[share.roommate.name] = display_cost
                    splits_str.append(f"{share.roommate.name}: {share.units:g} (${display_cost})")

            item_rows.append({
                'item': item,
                'splits_str': ', '.join(splits_str),
                'splits_by_roommate': [splits_dict[r.name] for r in roommates],
            })

        exact_tax_per_person = (
            (receipt.tax_amount / Decimal(num_roommates))
            if num_roommates > 0 else Decimal('0.00')
        )
        display_tax_per_person = exact_tax_per_person.quantize(Decimal('0.01'))
        true_receipt_total = (actual_items_sum + receipt.tax_amount).quantize(Decimal('0.01'))

        roommate_subtotals = []
        for r in roommates:
            items_rounded = r_items_exact[r].quantize(Decimal('0.01'))
            person_total = (r_items_exact[r] + exact_tax_per_person).quantize(Decimal('0.01'))
            overall_items[r] += r_items_exact[r]
            overall_tax[r] += exact_tax_per_person
            roommate_subtotals.append({
                'roommate': r,
                'items': items_rounded,
                'tax': display_tax_per_person,
                'total': person_total,
            })

        diff = true_receipt_total - sum(sub['total'] for sub in roommate_subtotals)
        if diff != Decimal('0.00') and roommate_subtotals:
            me_sub = next((s for s in roommate_subtotals if s['roommate'].is_me), roommate_subtotals[0])
            me_sub['total'] += diff

        receipt_reports.append({
            'receipt': receipt,
            'item_rows': item_rows,
            'tax_per_person': display_tax_per_person,
            'roommate_subtotals': roommate_subtotals,
            'receipt_grand_total': true_receipt_total,
        })

    summary_rows = []
    for r in roommates:
        items_sub = overall_items[r].quantize(Decimal('0.01'))
        tax_sub = overall_tax[r].quantize(Decimal('0.01'))
        final_tot = (overall_items[r] + overall_tax[r]).quantize(Decimal('0.01'))
        summary_rows.append({
            'roommate': r,
            'items_subtotal': items_sub,
            'tax_share': tax_sub,
            'final_total': final_tot,
        })

    true_grand_total = sum(rep['receipt_grand_total'] for rep in receipt_reports)
    overall_diff = true_grand_total - sum(row['final_total'] for row in summary_rows)
    if overall_diff != Decimal('0.00') and summary_rows:
        me_row = next((row for row in summary_rows if row['roommate'].is_me), summary_rows[0])
        me_row['final_total'] += overall_diff

    return {
        'receipts': receipts,
        'roommates': roommates,
        'receipt_reports': receipt_reports,
        'summary_rows': summary_rows,
        'grand_total': true_grand_total,
    }


@admin_required
def batch_summary(request):
    ids_param = request.GET.get('ids', '')
    receipt_ids = [int(x) for x in ids_param.split(',') if x.isdigit()]
    if not receipt_ids:
        return redirect('receipt_list')

    if request.method == 'POST' and 'receipt_id' in request.POST:
        r_obj = get_object_or_404(Receipt, id=int(request.POST['receipt_id']))
        r_obj.tax_amount = Decimal(request.POST.get('tax_amount') or '0.00')
        r_obj.save()
        sync_receipt_to_expense(r_obj)
        return redirect(f"{reverse('batch_summary')}?ids={ids_param}")

    data = build_summary_data(receipt_ids)
    data['ids_param'] = ids_param
    return render(request, 'splitter/summary.html', data)


@admin_required
def export_latex_pdf(request):
    ids_param = request.GET.get('ids', '')
    receipt_ids = [int(x) for x in ids_param.split(',') if x.isdigit()]
    if not receipt_ids:
        return redirect('receipt_list')

    data = build_summary_data(receipt_ids)
    roommates = data['roommates']

    lines = [
        r"\documentclass[11pt,a4paper]{article}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage[margin=0.75in]{geometry}",
        r"\usepackage{booktabs}",
        r"\usepackage{longtable}",
        r"\usepackage{xcolor}",
        r"\definecolor{tablehead}{HTML}{F0F4F8}",
        r"\begin{document}",
        r"\section*{Superstore Splitwise Summary}",
        f"\\textbf{{Bills Included:}} {len(data['receipts'])} \\quad | \\quad "
        f"\\textbf{{Combined Grand Total:}} \\${data['grand_total']}\\\\[1em]",
        r"\subsection*{Final Splitwise Totals (Tax Divided Equally)}",
        r"\begin{tabular}{lrrr}",
        r"\toprule",
        r"\textbf{Roommate} & \textbf{Items Subtotal} & \textbf{Equal Tax Share} & \textbf{Add to Splitwise} \\",
        r"\midrule",
    ]

    for row in data['summary_rows']:
        r_name = latex_escape(row['roommate'].name + (" (Me)" if row['roommate'].is_me else ""))
        lines.append(
            f"{r_name} & \\${row['items_subtotal']} & \\${row['tax_share']} & \\textbf{{\\${row['final_total']}}} \\\\"
        )

    lines += [
        r"\bottomrule",
        r"\end{tabular}",
        r"\vspace{1.5em}",
    ]

    col_spec = "p{5.2cm}rr" + ("r" * len(roommates))
    r_headers = " & ".join([f"\\textbf{{{latex_escape(r.name)}}}" for r in roommates])

    for rep in data['receipt_reports']:
        rec = rep['receipt']
        mod_str = rec.file_modified_at.strftime('%Y-%m-%d %H:%M') if rec.file_modified_at else 'N/A'
        date_info = f"Order: {latex_escape(rec.order_date_str)} | " if rec.order_date_str else ""
        lines += [
            f"\\subsection*{{Bill: {latex_escape(rec.filename)}}}",
            f"\\small{{{date_info}File Last Modified: {mod_str} \\quad | \\quad Total Bill Tax: \\${rec.tax_amount} (\\${rep['tax_per_person']} each)}}\\\\[0.5em]",
            f"\\begin{{longtable}}{{{col_spec}}}",
            r"\toprule",
            f"\\textbf{{Item}} & \\textbf{{Qty}} & \\textbf{{Price}} & {r_headers} \\\\",
            r"\midrule",
        ]
        for ir in rep['item_rows']:
            it = ir['item']
            r_cols = " & ".join([f"\\${val}" for val in ir['splits_by_roommate']])
            lines.append(
                f"{latex_escape(it.name[:38])} & {latex_escape(it.quantity_str)} & \\${it.total_price} & {r_cols} \\\\"
            )
        lines.append(r"\midrule")
        tax_cols = " & ".join([f"\\${rep['tax_per_person']}" for _ in roommates])
        lines.append(f"\\textit{{Equal Tax Share (1/4)}} & - & \\${rec.tax_amount} & {tax_cols} \\\\")
        lines.append(r"\midrule")
        tot_cols = " & ".join([f"\\textbf{{\\${sub['total']}}}" for sub in rep['roommate_subtotals']])
        lines.append(f"\\textbf{{Bill Total}} & & \\textbf{{\\${rep['receipt_grand_total']}}} & {tot_cols} \\\\")
        lines += [
            r"\bottomrule",
            r"\end{longtable}",
            r"\vspace{0.5em}",
        ]

    lines.append(r"\end{document}")
    tex_source = "\n".join(lines)

    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = os.path.join(tmpdir, "splitwise_summary.tex")
        pdf_path = os.path.join(tmpdir, "splitwise_summary.pdf")
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(tex_source)

        try:
            subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", "-output-directory", tmpdir, tex_path],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            with open(pdf_path, "rb") as pdf_file:
                response = HttpResponse(pdf_file.read(), content_type="application/pdf")
                response["Content-Disposition"] = 'attachment; filename="Splitwise_Monthly_Summary.pdf"'
                return response
        except (FileNotFoundError, subprocess.CalledProcessError):
            response = HttpResponse(tex_source, content_type="text/plain")
            response["Content-Disposition"] = 'attachment; filename="Splitwise_Monthly_Summary.tex"'
            return response


@login_required(login_url='login')
def analytics_dashboard(request):
    sync_receipts_folder()
    ensure_roommates()
    active_group = get_active_group(request)

    active_tab = request.GET.get("tab", "household")
    if active_tab not in ["household", "superstore"]:
        active_tab = "household"

    group_members = list(active_group.members.filter(is_active=True)) if active_group else []
    active_roommates = group_members if group_members else list(Roommate.objects.filter(is_active=True))

    splitwise_expenses = list(
        Expense.objects.filter(group=active_group, is_payment=False)
        .select_related("paid_by")
        .prefetch_related("splits__roommate")
        .order_by("date")
    )

    total_household_spent = Decimal("0.00")
    cat_totals = defaultdict(Decimal)
    cat_counts = defaultdict(int)
    monthly_totals = defaultdict(Decimal)
    monthly_dates = {}

    payer_totals = {r.name: Decimal("0.00") for r in active_roommates}
    share_totals = {r.name: Decimal("0.00") for r in active_roommates}

    for exp in splitwise_expenses:
        total_household_spent += exp.amount
        cat_totals[exp.category] += exp.amount
        cat_counts[exp.category] += 1

        m_key = exp.date.strftime("%Y-%m")
        monthly_totals[m_key] += exp.amount
        if m_key not in monthly_dates:
            monthly_dates[m_key] = exp.date

        if exp.paid_by.name in payer_totals:
            payer_totals[exp.paid_by.name] += exp.amount

        for sp in exp.splits.all():
            if sp.roommate.name in share_totals:
                share_totals[sp.roommate.name] += sp.amount

    sorted_months = sorted(monthly_totals.keys())
    month_labels = [monthly_dates[m].strftime("%b '%y") for m in sorted_months]
    month_totals_float = [float(monthly_totals[m]) for m in sorted_months]

    sorted_categories = sorted(
        [
            {
                "name": cat,
                "amount": amt.quantize(Decimal("0.01")),
                "count": cat_counts[cat],
                "pct": round((float(amt) / float(total_household_spent) * 100), 1) if total_household_spent > 0 else 0
            }
            for cat, amt in cat_totals.items()
        ],
        key=lambda x: x["amount"],
        reverse=True
    )

    roommate_comparisons = []
    for r in active_roommates:
        paid = payer_totals[r.name].quantize(Decimal("0.01"))
        share = share_totals[r.name].quantize(Decimal("0.01"))
        diff = (paid - share).quantize(Decimal("0.01"))
        pct = round((float(share) / float(total_household_spent) * 100), 1) if total_household_spent > 0 else 0
        roommate_comparisons.append({
            "roommate": r,
            "paid": paid,
            "share": share,
            "net_diff": diff,
            "pct": pct,
        })

    top_household_expenses = list(
        Expense.objects.filter(is_payment=False)
        .select_related("paid_by")
        .order_by("-amount")[:10]
    )

    household_count = len(splitwise_expenses)
    active_months_count = len(sorted_months)
    monthly_avg_spend = (
        (total_household_spent / Decimal(active_months_count)).quantize(Decimal("0.01"))
        if active_months_count else Decimal("0.00")
    )

    top_cat = sorted_categories[0] if sorted_categories else {"name": "General", "amount": Decimal("0.00"), "pct": 0}
    top_payer_name = max(payer_totals.items(), key=lambda x: x[1])[0] if payer_totals else "N/A"
    top_payer_amount = payer_totals.get(top_payer_name, Decimal("0.00")).quantize(Decimal("0.01"))

    household_chart_payload = {
        "months": month_labels,
        "monthly_totals": month_totals_float,
        "cat_labels": [c["name"] for c in sorted_categories],
        "cat_totals": [float(c["amount"]) for c in sorted_categories],
        "roommate_labels": [r.name for r in active_roommates],
        "roommate_paid": [float(payer_totals[r.name]) for r in active_roommates],
        "roommate_share": [float(share_totals[r.name]) for r in active_roommates],
    }

    # Superstore Grocery Receipt Data
    receipts = list(Receipt.objects.all().order_by("file_modified_at", "id"))
    num_active_rms = len(active_roommates)

    superstore_totals = {r.name: Decimal("0.00") for r in active_roommates}
    bill_labels = []
    bill_totals = []
    bill_roommate_series = {r.name: [] for r in active_roommates}
    product_stats = defaultdict(lambda: {"spent": Decimal("0.00"), "count": 0, "img": None})

    superstore_spent = Decimal("0.00")
    superstore_tax = Decimal("0.00")
    superstore_items_count = 0

    for receipt in receipts:
        items = list(receipt.items.prefetch_related("shares__roommate"))
        if not items:
            continue

        items_sum = sum((i.total_price for i in items), Decimal("0.00"))
        bill_total = (items_sum + receipt.tax_amount).quantize(Decimal("0.01"))
        superstore_spent += bill_total
        superstore_tax += receipt.tax_amount
        superstore_items_count += len(items)

        if receipt.order_date_str:
            label = receipt.order_date_str.split(",")[0].strip()
        else:
            label = receipt.filename.replace("Superstore_Order_", "#").replace(".html", "")[-6:]
        bill_labels.append(label)
        bill_totals.append(float(bill_total))

        r_exact = {r.name: Decimal("0.0000") for r in active_roommates}
        for item in items:
            key = item.name.strip()
            product_stats[key]["spent"] += item.total_price
            product_stats[key]["count"] += 1
            if item.image_url and not product_stats[key]["img"]:
                product_stats[key]["img"] = item.image_url

            tot_units = sum(s.units for s in item.shares.all())
            if tot_units > 0:
                for s in item.shares.all():
                    if s.roommate.name in r_exact:
                        r_exact[s.roommate.name] += item.total_price * (s.units / tot_units)

        tax_share = (receipt.tax_amount / Decimal(num_active_rms)) if num_active_rms else Decimal("0.00")
        for r in active_roommates:
            person_bill = (r_exact[r.name] + tax_share).quantize(Decimal("0.01"))
            superstore_totals[r.name] += person_bill
            bill_roommate_series[r.name].append(float(person_bill))

    top_products = sorted(
        [{"name": k, "spent": v["spent"].quantize(Decimal("0.01")), "count": v["count"], "img": v["img"]}
         for k, v in product_stats.items()],
        key=lambda x: x["spent"],
        reverse=True
    )[:8]

    max_product_spent = top_products[0]["spent"] if top_products else Decimal("1.00")
    for p in top_products:
        p["pct"] = int((p["spent"] / max_product_spent) * 100) if max_product_spent > 0 else 0

    bill_count = len(bill_labels)
    superstore_avg_bill = (superstore_spent / Decimal(bill_count)).quantize(Decimal("0.01")) if bill_count else Decimal("0.00")

    superstore_roommate_cards = []
    for r in active_roommates:
        amt = superstore_totals[r.name].quantize(Decimal("0.01"))
        pct = round((float(amt) / float(superstore_spent)) * 100, 1) if superstore_spent > 0 else 0
        superstore_roommate_cards.append({"roommate": r, "total": amt, "pct": pct})

    superstore_chart_payload = {
        "labels": bill_labels,
        "bill_totals": bill_totals,
        "roommate_names": [r.name for r in active_roommates],
        "roommate_totals": [float(superstore_totals[r.name]) for r in active_roommates],
        "roommate_series": [
            {"name": r.name, "data": bill_roommate_series[r.name]}
            for r in active_roommates
        ],
    }

    return render(request, "splitter/analytics.html", {
        "active_tab": active_tab,
        "active_group": active_group,
        "total_household_spent": total_household_spent.quantize(Decimal("0.01")),
        "household_count": household_count,
        "active_months_count": active_months_count,
        "monthly_avg_spend": monthly_avg_spend,
        "top_cat": top_cat,
        "top_payer_name": top_payer_name,
        "top_payer_amount": top_payer_amount,
        "sorted_categories": sorted_categories,
        "roommate_comparisons": roommate_comparisons,
        "top_household_expenses": top_household_expenses,
        "household_chart_json": json.dumps(household_chart_payload),
        "superstore_spent": superstore_spent.quantize(Decimal("0.01")),
        "superstore_tax": superstore_tax.quantize(Decimal("0.01")),
        "superstore_avg_bill": superstore_avg_bill,
        "bill_count": bill_count,
        "superstore_items_count": superstore_items_count,
        "superstore_roommate_cards": superstore_roommate_cards,
        "top_products": top_products,
        "superstore_chart_json": json.dumps(superstore_chart_payload),
    })


def manifest_view(request):
    """Serve PWA web app manifest with root scope."""
    manifest_path = os.path.join(settings.BASE_DIR, "rcsscrapper", "static", "manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as f:
        content = f.read()
    return HttpResponse(content, content_type="application/manifest+json")


def service_worker_view(request):
    """Serve PWA Service Worker script with root scope permission."""
    sw_path = os.path.join(settings.BASE_DIR, "rcsscrapper", "static", "sw.js")
    with open(sw_path, "r", encoding="utf-8") as f:
        content = f.read()
    response = HttpResponse(content, content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    return response


# ==========================================
# GROUP MANAGEMENT & SPLITWISE IMPORT VIEWS
# ==========================================

@login_required(login_url='login')
def group_list(request):
    """View and manage all household, trip, and shared expense groups."""
    active_group = get_active_group(request)
    all_groups = list(HouseholdGroup.objects.all().prefetch_related('members', 'expenses'))
    active_rms = list(Roommate.objects.filter(is_active=True))

    return render(request, 'splitter/group_list.html', {
        'groups': all_groups,
        'active_group': active_group,
        'available_roommates': active_rms,
    })


@login_required(login_url='login')
def select_group(request, group_id):
    """Switch the currently active working group."""
    group = HouseholdGroup.objects.filter(id=group_id).first()
    if not group:
        messages.warning(request, "Selected group was not found.")
        return redirect('splitwise_dashboard')
    request.session['active_group_id'] = group.id
    messages.success(request, f"Switched active group to '{group.name}'")
    next_url = request.GET.get('next') or reverse('splitwise_dashboard')
    return redirect(next_url)


@login_required(login_url='login')
def create_group(request):
    """Create a brand new group (e.g. House, Trip, Vacation)."""
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        group_type = request.POST.get('group_type', 'home')
        description = request.POST.get('description', '').strip()
        member_ids = request.POST.getlist('members')

        if not name:
            messages.error(request, "Group name cannot be blank.")
            return redirect('group_list')

        user_rm = getattr(request.user, 'roommate', None)
        group = HouseholdGroup.objects.create(
            name=name,
            group_type=group_type,
            description=description,
            created_by=user_rm
        )

        if user_rm:
            group.members.add(user_rm)

        for m_id in member_ids:
            if m_id.isdigit():
                rm = Roommate.objects.filter(id=int(m_id)).first()
                if rm:
                    group.members.add(rm)

        request.session['active_group_id'] = group.id
        messages.success(request, f"Group '{group.name}' created with {group.members.count()} members!")
        return redirect('splitwise_dashboard')

    return redirect('group_list')


@login_required(login_url='login')
def import_splitwise_view(request):
    """
    Import and clean Splitwise CSV export data into a target group.
    Supports file upload or 1-click loading from default server dataset (data/41_2026-10-02_export.csv).
    Optionally allows downloading the cleaned CAD CSV.
    """
    active_group = get_active_group(request)
    all_groups = list(HouseholdGroup.objects.all())

    if request.method == 'POST':
        action = request.POST.get('action', 'import')
        group_id = request.POST.get('group_id')
        target_group = HouseholdGroup.objects.filter(id=group_id).first() if group_id else active_group
        replace_existing = request.POST.get('replace_existing') == '1'

        file_obj = request.FILES.get('csv_file')
        use_server_file = request.POST.get('use_server_file') == '1'

        if use_server_file:
            server_csv = os.path.join(settings.BASE_DIR, 'data', '41_2026-10-02_export.csv')
            if not os.path.exists(server_csv):
                messages.error(request, "Default server Splitwise CSV file was not found in data/.")
                return redirect('import_splitwise')
            file_source = server_csv
        elif file_obj:
            file_source = file_obj
        else:
            messages.error(request, "Please choose a Splitwise CSV export file to upload or select the server file.")
            return redirect('import_splitwise')

        res = import_and_clean_splitwise_csv(
            file_source=file_source,
            target_group=target_group,
            clean_to_cad=True,
            filter_zero_members=True,
            replace_existing=replace_existing
        )

        if not res.get('success'):
            messages.error(request, f"Import error: {res.get('error')}")
            return redirect('import_splitwise')

        if action == 'download_cleaned_csv':
            response = HttpResponse(res['cleaned_csv_content'], content_type='text/csv')
            safe_name = target_group.name.replace(' ', '_').replace('/', '_')
            response['Content-Disposition'] = f'attachment; filename="cleaned_{safe_name}_cad.csv"'
            return response

        request.session['active_group_id'] = target_group.id
        messages.success(
            request,
            f"Successfully cleaned & imported {res['total_imported']} transactions "
            f"({res['expenses_created']} expenses, {res['payments_created']} payments) "
            f"into '{target_group.name}'! All figures standardized in CAD."
        )
        return redirect('splitwise_dashboard')

    return render(request, 'splitter/import_splitwise.html', {
        'active_group': active_group,
        'all_groups': all_groups,
    })
