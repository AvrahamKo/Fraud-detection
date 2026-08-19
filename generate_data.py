"""
generate_data.py
--------------------------------------------------------------------
Mock data generator for the Financial Fraud Detection Dashboard.

Creates 10,000 payment transactions and stores them in a SQLite
database (transactions.db, table: transactions).

The dataset is mostly "normal" traffic, with three fraud patterns
deliberately injected so the SQL detection logic in app.py has
something real to find:

    1. Impossible Travel  - same user, two countries, < 30 minutes apart
    2. Amount Anomaly     - a payment far above the user's own average
    3. Card Testing       - several tiny FAILED payments, then one
                            large SUCCESSFUL payment shortly after

Run:  python generate_data.py
--------------------------------------------------------------------
"""

import os
import random
import sqlite3
from datetime import datetime, timedelta

# ---------------------------------------------------------------------
# 1. Configuration
# ---------------------------------------------------------------------

RANDOM_SEED = 42          # fixed seed -> the dataset is reproducible
DB_FILE = "transactions.db"
TABLE_NAME = "transactions"

TOTAL_ROWS = 10_000       # exact number of rows requested
N_USERS = 250             # number of distinct customers

# How many fraud cases to inject (kept small: fraud is rare by nature)
N_IMPOSSIBLE_TRAVEL = 25  # each case = 2 rows
N_AMOUNT_ANOMALY = 30     # each case = 1 row
N_CARD_TESTING = 20       # each case = 4-6 failed rows + 1 success row

# Simulation window: the last 90 days
END_DATE = datetime(2025, 12, 31, 23, 59, 59)
START_DATE = END_DATE - timedelta(days=90)

# Each user "lives" in one home country, so a country switch a few
# minutes apart is physically impossible and a strong fraud signal.
COUNTRIES = ["USA", "UK", "Germany", "France", "Israel",
             "Canada", "Australia", "Japan", "Brazil", "Spain"]

# Countries used for the second leg of "impossible travel": far away
# from anywhere else in the list, so the pattern is unambiguous.
FAR_AWAY_COUNTRIES = ["Nigeria", "Russia", "Indonesia", "Vietnam", "Ukraine"]

random.seed(RANDOM_SEED)


# ---------------------------------------------------------------------
# 2. Small helper functions
# ---------------------------------------------------------------------

def random_ip():
    """Return a random public-looking IPv4 address."""
    return ".".join(str(random.randint(11, 240)) for _ in range(4))


def random_timestamp():
    """Return a random datetime inside the simulation window."""
    seconds_in_window = int((END_DATE - START_DATE).total_seconds())
    return START_DATE + timedelta(seconds=random.randint(0, seconds_in_window))


def fmt(ts):
    """
    Format a datetime as an ISO-like string.

    SQLite has no native DATETIME type, so timestamps are stored as TEXT
    in "YYYY-MM-DD HH:MM:SS" format. That is the format SQLite's own
    date/time functions understand, which lets the detection queries use
    julianday() and ORDER BY directly on the column.
    """
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def normal_amount():
    """
    Typical e-commerce purchase amount.

    A log-normal distribution is used because real spending is
    right-skewed: many small purchases, few large ones.
    """
    return round(min(random.lognormvariate(mu=4.0, sigma=0.7), 2000), 2)


# ---------------------------------------------------------------------
# 3. User profiles
# ---------------------------------------------------------------------

def build_user_profiles():
    """
    Give every user a stable identity: a home country and a home IP range.
    Anchoring users to one country is what makes the injected
    "impossible travel" cases stand out from normal behaviour.
    """
    profiles = {}
    for i in range(1, N_USERS + 1):
        user_id = "U{:04d}".format(i)
        profiles[user_id] = {
            "home_country": random.choice(COUNTRIES),
            "ip_prefix": "{}.{}".format(random.randint(11, 240), random.randint(0, 255)),
        }
    return profiles


def user_ip(profile):
    """Build an IP address inside the user's usual network range."""
    return "{}.{}.{}".format(profile["ip_prefix"],
                             random.randint(0, 255),
                             random.randint(1, 254))


# ---------------------------------------------------------------------
# 4. Normal (legitimate) traffic
# ---------------------------------------------------------------------

def generate_normal_transactions(profiles, n_rows):
    """
    Build the baseline of legitimate activity.

    Realistic details kept on purpose:
      * ~4% of normal payments fail (wrong CVV, insufficient funds, ...)
      * ~3% happen while the customer is genuinely abroad
      * amounts follow a realistic, right-skewed spending distribution
    """
    rows = []
    user_ids = list(profiles.keys())

    for _ in range(n_rows):
        user_id = random.choice(user_ids)
        profile = profiles[user_id]

        # Occasional legitimate trip abroad
        if random.random() < 0.03:
            country = random.choice(
                [c for c in COUNTRIES if c != profile["home_country"]]
            )
        else:
            country = profile["home_country"]

        status = "failed" if random.random() < 0.04 else "success"

        rows.append({
            "timestamp": random_timestamp(),
            "user_id": user_id,
            "ip_address": user_ip(profile),
            "country": country,
            "amount": normal_amount(),
            "status": status,
        })

    return rows


# ---------------------------------------------------------------------
# 5. Anomaly 1 - Impossible Travel
# ---------------------------------------------------------------------

def generate_impossible_travel(profiles, n_cases):
    """
    Same user pays from two distant countries within 30 minutes.

    Physically impossible, so the credentials are almost certainly being
    used by two different people (classic account takeover).
    """
    rows = []
    user_ids = random.sample(list(profiles.keys()), n_cases)

    for user_id in user_ids:
        profile = profiles[user_id]
        first_ts = random_timestamp()
        # Second leg 2-25 minutes later: no flight covers that distance
        second_ts = first_ts + timedelta(minutes=random.randint(2, 25))

        # Leg 1: normal payment from home
        rows.append({
            "timestamp": first_ts,
            "user_id": user_id,
            "ip_address": user_ip(profile),
            "country": profile["home_country"],
            "amount": normal_amount(),
            "status": "success",
        })

        # Leg 2: payment from a far-away country and a foreign IP
        rows.append({
            "timestamp": second_ts,
            "user_id": user_id,
            "ip_address": random_ip(),
            "country": random.choice(FAR_AWAY_COUNTRIES),
            "amount": round(random.uniform(150, 900), 2),
            "status": "success",
        })

    return rows


# ---------------------------------------------------------------------
# 6. Anomaly 2 - Amount Anomaly
# ---------------------------------------------------------------------

def generate_amount_anomaly(profiles, n_cases):
    """
    A single payment far larger than what this specific user normally
    spends - typically the "cash-out" step after an account takeover.

    This is a *relative* rule: 15,000 may be normal for a business
    account but extreme for someone who usually spends 60.
    """
    rows = []
    user_ids = random.sample(list(profiles.keys()), n_cases)

    for user_id in user_ids:
        profile = profiles[user_id]
        rows.append({
            "timestamp": random_timestamp(),
            "user_id": user_id,
            "ip_address": user_ip(profile),
            "country": profile["home_country"],
            "amount": round(random.uniform(9000, 25000), 2),
            "status": "success",
        })

    return rows


# ---------------------------------------------------------------------
# 7. Anomaly 3 - Card Testing
# ---------------------------------------------------------------------

def generate_card_testing(profiles, n_cases):
    """
    Classic card-testing / enumeration attack:

        step 1 - the fraudster validates stolen cards with tiny payments
                 (most of them FAIL)
        step 2 - once a card works, they immediately cash out with one
                 large SUCCESSFUL payment

    The signal is the burst of small failures followed by a big success
    inside a short time window.
    """
    rows = []
    user_ids = random.sample(list(profiles.keys()), n_cases)

    for user_id in user_ids:
        profile = profiles[user_id]
        burst_start = random_timestamp()
        ip = random_ip()  # the attacker's IP, not the customer's usual one

        # Step 1: 4-6 small failed attempts, a few minutes apart
        n_attempts = random.randint(4, 6)
        for i in range(n_attempts):
            rows.append({
                "timestamp": burst_start + timedelta(minutes=i * random.randint(1, 3)),
                "user_id": user_id,
                "ip_address": ip,
                "country": profile["home_country"],
                "amount": round(random.uniform(0.5, 9.99), 2),
                "status": "failed",
            })

        # Step 2: the cash-out, 15-40 minutes after the burst started
        rows.append({
            "timestamp": burst_start + timedelta(minutes=random.randint(15, 40)),
            "user_id": user_id,
            "ip_address": ip,
            "country": profile["home_country"],
            "amount": round(random.uniform(700, 4500), 2),
            "status": "success",
        })

    return rows


# ---------------------------------------------------------------------
# 8. Assemble the dataset
# ---------------------------------------------------------------------

def build_dataset():
    """
    Combine normal traffic and injected fraud into exactly TOTAL_ROWS
    rows, sorted by time, with sequential transaction IDs.
    """
    profiles = build_user_profiles()

    fraud_rows = (
        generate_impossible_travel(profiles, N_IMPOSSIBLE_TRAVEL)
        + generate_amount_anomaly(profiles, N_AMOUNT_ANOMALY)
        + generate_card_testing(profiles, N_CARD_TESTING)
    )

    # Normal rows fill whatever is left, so the total is exactly 10,000
    n_normal = TOTAL_ROWS - len(fraud_rows)
    all_rows = generate_normal_transactions(profiles, n_normal) + fraud_rows

    # Sort chronologically so transaction_id grows with time, exactly
    # like a real production transactions table.
    all_rows.sort(key=lambda r: r["timestamp"])

    final_rows = []
    for i, row in enumerate(all_rows, start=1):
        final_rows.append((
            i,                       # transaction_id
            fmt(row["timestamp"]),   # timestamp (TEXT, sortable)
            row["user_id"],
            row["ip_address"],
            row["country"],
            row["amount"],
            row["status"],
        ))

    return final_rows


# ---------------------------------------------------------------------
# 9. Write to SQLite
# ---------------------------------------------------------------------

def save_to_sqlite(rows):
    """Create a fresh transactions.db and load all rows into it."""
    # Start from scratch on every run so the script is safely repeatable
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)

    connection = sqlite3.connect(DB_FILE)
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE transactions (
            transaction_id INTEGER PRIMARY KEY,
            timestamp      TEXT    NOT NULL,
            user_id        TEXT    NOT NULL,
            ip_address     TEXT    NOT NULL,
            country        TEXT    NOT NULL,
            amount         REAL    NOT NULL,
            status         TEXT    NOT NULL
        )
    """)

    cursor.executemany(
        "INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )

    # Indexes on the columns the detection queries filter and sort by.
    # On 10k rows this barely matters, but it is the right instinct.
    cursor.execute("CREATE INDEX idx_user_time ON transactions (user_id, timestamp)")
    cursor.execute("CREATE INDEX idx_status ON transactions (status)")

    connection.commit()
    connection.close()


# ---------------------------------------------------------------------
# 10. Entry point
# ---------------------------------------------------------------------

if __name__ == "__main__":
    rows = build_dataset()
    save_to_sqlite(rows)

    print("Created '{}' with {:,} transactions.".format(DB_FILE, len(rows)))
    print("  Date range          : {}  ->  {}".format(rows[0][1], rows[-1][1]))
    print("  Distinct users      : {}".format(N_USERS))
    print("Injected fraud patterns:")
    print("  Impossible travel   : {} cases".format(N_IMPOSSIBLE_TRAVEL))
    print("  Amount anomalies    : {} cases".format(N_AMOUNT_ANOMALY))
    print("  Card testing bursts : {} cases".format(N_CARD_TESTING))
