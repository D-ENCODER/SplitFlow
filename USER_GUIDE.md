# SplitFlow User Guide and Operational Manual

Welcome to the **SplitFlow** user documentation. This manual covers all operational features of the application, including initial account registration, group administration, expense allocation, debt simplification, Splitwise migration, and automated grocery receipt reconciliation.

---

## 1. Initial Onboarding and Account Provisioning

When accessing a fresh SplitFlow deployment with zero existing data:

1. **User Registration**:
   - Access `/register/` from the login interface.
   - Enter your `Username`, `Full Name`, and secure password.
   - Upon form submission, the system creates your `User` authentication account and associates an active `Roommate` profile.
2. **Automated Household Provisioning**:
   - The onboarding engine automatically initializes your primary household group, titled `"<Full Name>'s Household"`.
   - Your account is assigned as the **Group Admin** with exclusive administrative authority.
3. **Empty-State Dashboard**:
   - The dashboard opens directly into a clean, crash-free `$0.00` settled state.

---

## 2. Group Governance and Member Management

SplitFlow implements a role-based access control (RBAC) model to maintain group boundaries and prevent unauthorized changes.

### 2.1. Group Admin Privileges
The creator of a household or group possesses administrative rights:
- **Rename Group**: Modify display name, group type (home, trip, other), and operational description.
- **Roster Management**:
  - Add existing registered users by matching username or email.
  - Create placeholder roommate profiles for participants who have not yet created accounts.
  - Remove members from the household (the Group Admin cannot remove themselves to prevent orphaned groups).
- **Group Deletion**: Permanently retire a group and its historical relationships if no longer needed.

### 2.2. Standard Member Privileges
Standard members who have been added to a household can:
- Record new shared expenses and equalizing settlements.
- View real-time balance matrices and historical ledgers.
- Inspect graphical spending analytics.
- Members cannot modify group settings or alter member rosters.

---

## 3. Recording and Allocating Expenses

### 3.1. Server-Enforced Date Integrity
To ensure chronological auditing and eliminate retroactive debt disputes, the expense date is strictly determined server-side:
- Every newly created expense is locked to `timezone.now().date()` upon submission.
- The user interface displays a read-only date indicator.
- If an expense corresponds to a past event or delayed entry, relevant context must be recorded in the **Notes** field (e.g., *"Concert tickets purchased last weekend"*).

### 3.2. Allocation Methods
- **Equal Split**: Evenly divides the total cost among all selected participants.
- **Custom Split**: Allows exact dollar assignments per participant. The application validates that the sum of all individual splits matches the total transaction amount before committing to the database.

---

## 4. Debt Simplification and Equalization

### 4.1. Graph-Theoretic Min-Cash-Flow Simplification
Rather than requiring every individual to execute separate bilateral bank transfers, SplitFlow processes the entire group ledger through a greedy Min-Cash-Flow graph algorithm:
1. Computes total net balance per member:
   $$\text{Net}_i = \sum \text{Paid}_i - \sum \text{Owed}_i$$
2. Iteratively pairs the largest debtor with the largest creditor.
3. Minimizes total transfers required to balance the entire group to at most $N - 1$ payments.

### 4.2. Executing a Settlement
1. Click **Settle Up** on the dashboard.
2. Select the paying member (debtor) and receiving member (creditor).
3. Confirm the payment amount.
4. The system inserts an equalization record with `is_settlement=True`, updating the ledger immediately.

---

## 5. Splitwise CSV Ingestion

To migrate historical ledgers from Splitwise into SplitFlow:
1. In Splitwise, navigate to your group settings and select **Export as CSV**.
2. In SplitFlow, navigate to **Groups** -> **Import from Splitwise** (`/groups/import-splitwise/`).
3. Select your CSV file. Choose whether to import into the current active household or create a newly named group.
4. The ingestion engine parses currency columns, maps existing participant names via fuzzy resolution, inserts historical expenses, and reconstructs the group's current balance matrix.

---

## 6. Automated Superstore Grocery Receipt Processing

SplitFlow includes an ingestion pipeline specifically tailored for Real Canadian Superstore (PC Express) digital orders:

1. **Digital Receipt Sync**:
   - Digital orders placed in `rcsscrapper/receipts_inbox/` are scanned and synchronized automatically.
   - Alternatively, orders can be ingested via the REST API endpoint (`/api/ingest/`).
2. **Itemization and Image Extraction**:
   - High-precision DOM parser extracts item names, unit quantities, package weights, and line-item prices.
   - Fetches product thumbnails directly from Superstore CDN endpoints.
3. **Penny-Perfect Reconciliation**:
   - The engine computes:
     $$\Delta = \text{Total Billed} - \left(\sum \text{Item Prices} + \text{Tax}\right)$$
   - Identifies bottle deposit fees, environmental handling charges, and promotional adjustments, generating explicit line items so the parsed sum matches the final credit card statement to the exact penny.
4. **Interactive Assignment Workflow**:
   - Navigate to **Superstore Inbox** (admin access required).
   - Use the item assignment interface to allocate individual items to specific roommates or split shared pantry staples evenly.
   - Review calculated summaries and commit approved splits directly to the household ledger.
5. **PDF and LaTeX Export**:
   - Generate formal accounting statements in PDF format via LaTeX templates for archiving.

---

## 7. Spending Analytics

Navigate to the **Analytics** tab (`/analytics/`) to inspect:
- Monthly expenditure progression and historical trendlines.
- Spending breakdowns categorized by department (Groceries, Utilities, Household, Rent).
- Roommate share distribution charts detailing net financial commitments.

---

## 8. Progressive Web Application (PWA)

SplitFlow functions as a Progressive Web Application with native desktop and mobile capabilities:
- **Installation**: Click the **Install App** button in the navigation header (or choose "Add to Home Screen" in your browser).
- **Offline Shell**: Service workers cache static assets for fast load times.
- **Display**: Operates in standalone, full-screen mode without browser address bars.
