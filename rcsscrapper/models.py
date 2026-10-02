from django.db import models
from decimal import Decimal

class Roommate(models.Model):
    name = models.CharField(max_length=50)
    is_me = models.BooleanField(default=False)

    def __str__(self):
        return self.name

class Receipt(models.Model):
    filename = models.CharField(max_length=255, unique=True)
    tax_amount = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0.00'))
    file_modified_at = models.DateTimeField(null=True, blank=True)
    order_date_str = models.CharField(max_length=100, blank=True, default='')
    processed = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)  # <-- Hides bill without re-importing the HTML file
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