from decimal import Decimal
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Roommate(models.Model):
    user = models.OneToOneField(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='roommate')
    name = models.CharField(max_length=50)
    is_me = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    email = models.EmailField(blank=True, default='')

    def __str__(self):
        return self.name

    @property
    def display_name(self):
        return f"{self.name} (Me)" if self.is_me else self.name


class HouseholdGroup(models.Model):
    GROUP_TYPE_CHOICES = [
        ('home', 'Apartment / House'),
        ('trip', 'Trip / Vacation'),
        ('other', 'Other Group'),
    ]

    name = models.CharField(max_length=150)
    description = models.TextField(blank=True, default='')
    group_type = models.CharField(max_length=30, choices=GROUP_TYPE_CHOICES, default='home')
    members = models.ManyToManyField(Roommate, related_name='groups', blank=True)
    created_by = models.ForeignKey(Roommate, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_groups')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def member_count(self):
        return self.members.count()

    @property
    def type_icon(self):
        if self.group_type == 'home':
            return '🏠'
        elif self.group_type == 'trip':
            return '✈️'
        return '👥'


class Receipt(models.Model):
    group = models.ForeignKey(HouseholdGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name='receipts')
    filename = models.CharField(max_length=255, unique=True)
    tax_amount = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0.00'))
    file_modified_at = models.DateTimeField(null=True, blank=True)
    order_date_str = models.CharField(max_length=100, blank=True, default='')
    processed = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)  # Hides bill without re-importing the HTML file
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def total_bill(self):
        items_sum = sum((item.total_price for item in self.items.all()), Decimal('0.00'))
        return (items_sum + self.tax_amount).quantize(Decimal('0.01'))


class ReceiptItem(models.Model):
    receipt = models.ForeignKey(Receipt, related_name='items', on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    quantity_str = models.CharField(max_length=50)
    total_price = models.DecimalField(max_digits=8, decimal_places=2)
    image_url = models.URLField(max_length=500, blank=True, null=True)
    is_assigned = models.BooleanField(default=False)


class ItemShare(models.Model):
    item = models.ForeignKey(ReceiptItem, related_name='shares', on_delete=models.CASCADE)
    roommate = models.ForeignKey(Roommate, on_delete=models.CASCADE)
    units = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))


class Expense(models.Model):
    CATEGORY_CHOICES = [
        ('General', 'General'),
        ('Groceries', 'Groceries'),
        ('Utilities', 'Utilities'),
        ('Rent', 'Rent'),
        ('Dining', 'Dining Out'),
        ('Entertainment', 'Entertainment'),
        ('Household', 'Household Supplies'),
        ('Transportation', 'Transportation'),
        ('Furniture', 'Furniture'),
        ('Gifts', 'Gifts'),
        ('Payment', 'Payment'),
    ]

    group = models.ForeignKey(HouseholdGroup, on_delete=models.CASCADE, related_name='expenses', null=True, blank=True)
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateField(default=timezone.now)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='General')
    paid_by = models.ForeignKey(Roommate, on_delete=models.CASCADE, related_name='expenses_paid')
    notes = models.TextField(blank=True, default='')

    # Settlement payment between two people
    is_payment = models.BooleanField(default=False)
    payment_to = models.ForeignKey(Roommate, on_delete=models.CASCADE, null=True, blank=True, related_name='payments_received')

    # Optional link to Superstore receipt
    receipt = models.OneToOneField(Receipt, on_delete=models.SET_NULL, null=True, blank=True, related_name='expense')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        if self.is_payment:
            to_name = self.payment_to.name if self.payment_to else 'Unknown'
            return f"Payment: {self.paid_by.name} -> {to_name}: ${self.amount}"
        return f"{self.description} (${self.amount}) paid by {self.paid_by.name}"


class ExpenseSplit(models.Model):
    expense = models.ForeignKey(Expense, related_name='splits', on_delete=models.CASCADE)
    roommate = models.ForeignKey(Roommate, related_name='expense_splits', on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        unique_together = ('expense', 'roommate')

    def __str__(self):
        return f"{self.roommate.name} owes ${self.amount} for {self.expense.description}"
