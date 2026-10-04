from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from rcsscrapper.models import Roommate, Receipt, ReceiptItem, ItemShare, Expense, ExpenseSplit, HouseholdGroup

admin.site.site_header = "SplitFlow Administration"
admin.site.site_title = "SplitFlow Admin Portal"
admin.site.index_title = "Household Ledger & Superstore Management"


class ExpenseSplitInline(admin.TabularInline):
    model = ExpenseSplit
    extra = 0
    fields = ('roommate', 'amount')


class ReceiptItemInline(admin.TabularInline):
    model = ReceiptItem
    extra = 0
    fields = ('name', 'quantity_str', 'total_price', 'is_assigned')


@admin.register(HouseholdGroup)
class HouseholdGroupAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'group_type', 'members_count_display', 'expenses_count_display', 'created_by', 'created_at')
    list_filter = ('group_type', 'created_at')
    search_fields = ('name', 'description')
    filter_horizontal = ('members',)
    ordering = ('name',)

    @admin.display(description="Members")
    def members_count_display(self, obj):
        return f"{obj.members.count()} members"

    @admin.display(description="Expenses")
    def expenses_count_display(self, obj):
        return f"{obj.expenses.count()} expenses"


@admin.register(Roommate)
class RoommateAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'user_account', 'email', 'is_me', 'is_active', 'total_expenses_paid')
    list_filter = ('is_active', 'is_me')
    search_fields = ('name', 'email', 'user__username')
    ordering = ('-is_active', 'name')
    actions = ['activate_roommates', 'deactivate_roommates']

    @admin.display(description="User Account")
    def user_account(self, obj):
        if obj.user:
            return format_html(
                '<span style="font-family:monospace; background:#e0e7ff; color:#3730a3; padding:2px 6px; border-radius:4px; font-weight:600;">@{}</span>',
                obj.user.username
            )
        return mark_safe('<span style="color:#94a3b8; font-style:italic;">No user</span>')

    @admin.display(description="Expenses Paid")
    def total_expenses_paid(self, obj):
        count = obj.expenses_paid.count()
        return f"{count} expenses"

    @admin.action(description="Mark selected roommates as active")
    def activate_roommates(self, request, queryset):
        queryset.update(is_active=True)

    @admin.action(description="Mark selected roommates as inactive")
    def deactivate_roommates(self, request, queryset):
        queryset.update(is_active=False)


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('id', 'date', 'group', 'description', 'amount_badge', 'category_badge', 'paid_by', 'is_payment', 'payment_to')
    list_filter = ('group', 'category', 'is_payment', 'date', 'paid_by')
    search_fields = ('description', 'notes', 'paid_by__name')
    date_hierarchy = 'date'
    ordering = ('-date', '-id')
    inlines = [ExpenseSplitInline]

    @admin.display(description="Amount", ordering='amount')
    def amount_badge(self, obj):
        if obj.is_payment:
            color = "#0284c7"
            bg = "#e0f2fe"
        else:
            color = "#059669"
            bg = "#d1fae5"
        amt_str = f"{obj.amount:.2f}"
        return format_html(
            '<span style="font-family:monospace; background:{}; color:{}; padding:3px 8px; border-radius:6px; font-weight:bold;">${}</span>',
            bg, color, amt_str
        )

    @admin.display(description="Category", ordering='category')
    def category_badge(self, obj):
        palette = {
            'Groceries': ('#059669', '#d1fae5'),
            'Utilities': ('#d97706', '#fef3c7'),
            'Rent': ('#e11d48', '#ffe4e6'),
            'Dining': ('#ea580c', '#ffedd5'),
            'Payment': ('#4f46e5', '#e0e7ff'),
        }
        color, bg = palette.get(obj.category, ('#475569', '#f1f5f9'))
        return format_html(
            '<span style="background:{}; color:{}; padding:2px 8px; border-radius:12px; font-size:11px; font-weight:600;">{}</span>',
            bg, color, obj.category
        )


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ('id', 'filename', 'group', 'order_date_str', 'total_bill_display', 'tax_amount', 'items_count_display', 'processed_badge', 'is_archived')
    list_filter = ('group', 'processed', 'is_archived', 'file_modified_at')
    search_fields = ('filename', 'order_date_str')
    ordering = ('-file_modified_at', '-id')
    inlines = [ReceiptItemInline]

    @admin.display(description="Total Bill")
    def total_bill_display(self, obj):
        bill_str = f"{obj.total_bill:.2f}"
        return format_html('<span style="font-family:monospace; font-weight:bold;">${}</span>', bill_str)

    @admin.display(description="Items")
    def items_count_display(self, obj):
        return f"{obj.items.count()} items"

    @admin.display(description="Status")
    def processed_badge(self, obj):
        if obj.processed:
            return mark_safe('<span style="background:#d1fae5; color:#059669; padding:2px 6px; border-radius:4px; font-weight:600; font-size:11px;">✓ Assigned</span>')
        return mark_safe('<span style="background:#fef3c7; color:#d97706; padding:2px 6px; border-radius:4px; font-weight:600; font-size:11px;">Pending</span>')


@admin.register(ReceiptItem)
class ReceiptItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'receipt_link', 'quantity_str', 'total_price', 'is_assigned')
    list_filter = ('is_assigned',)
    search_fields = ('name', 'receipt__filename')
    ordering = ('-id',)

    @admin.display(description="Receipt")
    def receipt_link(self, obj):
        return obj.receipt.filename


@admin.register(ExpenseSplit)
class ExpenseSplitAdmin(admin.ModelAdmin):
    list_display = ('id', 'expense', 'roommate', 'amount')
    list_filter = ('roommate',)
    search_fields = ('expense__description', 'roommate__name')


@admin.register(ItemShare)
class ItemShareAdmin(admin.ModelAdmin):
    list_display = ('id', 'item', 'roommate', 'units')
    list_filter = ('roommate',)
    search_fields = ('item__name', 'roommate__name')
