# 📖 User Guide & Handbook: Superstore Household Splitter

Welcome to the **Superstore Household Splitter** user guide! This handbook walks you through every feature of the platform, from day-one onboarding to advanced receipt parsing and debt settlement.

---

## 🚀 1. Getting Started (Zero-Data Onboarding)

When you launch the app for the very first time:
1. **Sign Up**: Navigate to `/register/` and fill in your Username, Full Name, and Password.
2. **Automatic Household Provisioning**:
   - The platform automatically creates a household named `"<Your Name>'s Household"`.
   - You are automatically designated as the **Group Admin (👑)** for this household.
3. **Adding Roommates**:
   - Head to the **Households / Groups** tab from the top navigation.
   - Click **Manage Group & Members** on your household card.
   - You can add registered users or create placeholder roommate profiles directly.

---

## 👑 2. Group Admin & Role-Based Access Control

Every household has clear governance:
- **Group Creator / Admin**:
  - The person who creates the group is its Admin.
  - Can change group name and settings.
  - Can add and remove members (the Admin cannot remove themselves).
  - Can delete the group if no longer needed.
- **Group Members**:
  - Can view expenses, analytics, and group balances.
  - Can record new expenses and settle up debts.
  - Cannot alter group membership or delete the group.

---

## 💳 3. Adding & Tracking Expenses

To record a shared purchase:
1. Click **+ Add Expense** on the dashboard.
2. **Enforced Current Date**:
   - The date field is strictly locked to **Today's Date** (`YYYY-MM-DD`) server-side.
   - This guarantees immutable chronological integrity and prevents retroactive tampering.
   - *Need to note when an event took place?* Use the **Notes** section (e.g., *"Dinner from Friday night"*).
3. **Split Options**:
   - **Equal Split**: Automatically divided among all selected roommates.
   - **Custom Split**: Assign exact custom dollar shares per roommate.

---

## 🤝 4. Settling Up (Greedy Min-Cash-Flow)

Instead of complex circular bank transfers, our system uses an optimized **Min-Cash-Flow** simplification algorithm:
- Total net credit/debt is computed across all members.
- Transactions are simplified to minimize total cash transfers.
- Click **Settle Up** -> Choose debtor & creditor -> Confirm payment.
- Settle-up payments automatically balance historical ledgers.

---

## 📥 5. Splitwise CSV Import

Migrating from Splitwise?
1. In Splitwise, go to your group settings -> **Export as CSV**.
2. In Superstore Splitter, navigate to **Households** -> **Import from Splitwise**.
3. Select your CSV file and optionally choose whether to create a new group or import into an existing one.
4. The system parses all historical transactions, maps participants, and rebuilds your ledger instantly.

---

## 🧾 6. Superstore Receipt Ingestion & Parsing

Designed specifically for Real Canadian Superstore orders:
- **Automated Ingestion**: Ingests digital receipts via API or automated email scraper.
- **Accurate Itemization**: Reconciles item descriptions, multi-buy savings, bottle deposits, and sales tax to match final paid amounts down to the exact penny.
- **Batch Assignment**: Quickly tag items to individual roommates or split common grocery items evenly.
- **PDF Export**: Generate clean LaTeX / PDF expense summaries for your records.

---

## 📊 7. Visual Analytics

Track your household spending:
- Monthly expenditure breakdown and historical trends.
- Roommate share distribution charts.
- Spending category insights (Groceries, Utilities, Household items).

---

## 📱 8. Progressive Web App (PWA)

Install Superstore Splitter as a native-like app on Android, iOS, or Desktop:
- **Android / Chrome**: Tap the three-dot menu -> **Add to Home screen** or click the install prompt.
- **iOS / Safari**: Tap **Share** -> **Add to Home Screen**.
- Enjoy standalone full-screen view, fast offline asset caching, and a clean mobile UI.
