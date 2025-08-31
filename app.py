# app.py — per-user master files with basic auth (local)
import streamlit as st
import pandas as pd
import numpy as np
import re
import io
import os
import json
import hashlib
from datetime import datetime
from supabase import create_client
from dotenv import load_dotenv
# ----------------- CONFIG -----------------
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY") 
USERS_FILE = "users.json"   # stores {"username": "salt$hexdigest", ...}
DATA_DIR = "."              # directory where user masters are stored (use absolute path if needed)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
# ----------------- Utils: auth & users -----------------
def load_users():
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_users(u):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(u, f, indent=2)

def make_salt(nbytes=16):
    return os.urandom(nbytes).hex()

def hash_password(password: str, salt: str):
    # returns hex digest of sha256(salt + password)
    h = hashlib.sha256()
    h.update((salt + password).encode("utf-8"))
    return h.hexdigest()

def create_user(username: str, password: str):
    users = load_users()
    username = username.lower()
    if username in users:
        return False, "Username already exists"
    salt = make_salt()
    users[username] = f"{salt}${hash_password(password, salt)}"
    save_users(users)
    return True, "Registered successfully"

def authenticate_user(username: str, password: str):
    users = load_users()
    username = username.lower()
    if username not in users:
        return False
    val = users[username]
    if "$" not in val:
        return False
    salt, pw_hash = val.split("$", 1)
    return hash_password(password, salt) == pw_hash

def sanitize_username(username: str):
    # keep lowercase alnum, dash, underscore
    u = username.lower().strip()
    u = re.sub(r"[^a-z0-9_-]", "_", u)
    return u

def user_master_path(username: str):
    username = sanitize_username(username)
    return os.path.join(DATA_DIR, f"master_{username}.xlsx")

def atomic_save_excel(df: pd.DataFrame, path: str):
    tmp = path + ".tmp"
    df.to_excel(tmp, index=False)
    os.replace(tmp, path)  # atomic on most OSes


def user_master_path(user_id: str) -> str:
    return f"{user_id}/master.json"   # inside 'masters' bucket

def get_or_create_master(user_id: str, username: str):
    path = user_master_path(user_id)

    # Step 1: Check DB
    existing = supabase.table("masters").select("*").eq("user_id", user_id).execute()

    if existing.data:
        # Fetch from storage
        file = supabase.storage.from_("masters").download(path)
        return file, path   # ✅ return both

    else:
        # Step 2: Create in storage (only if not exists)
        initial_content = b"{}"

        supabase.storage.from_("masters").upload(
            path,
            initial_content,
            {"upsert": False}
        )

        # Step 3: Insert into DB
        supabase.table("masters").insert({
            "user_id": user_id,
            "filename": "master.json",
            "storage_path": path,
            "uploaded_at": datetime.utcnow().isoformat()
        }).execute()

        return initial_content, path   # ✅ return both


# ----------------- App UI: login/register -----------------
st.set_page_config(page_title="Momentum Investing", layout="wide")
st.title("Momentum Investing ")

st.sidebar.header("Login / Register (required)")
if "user" not in st.session_state:
    st.session_state.user = None

users = load_users()

mode = st.sidebar.radio("Action", ["Login", "Register"])

if mode == "Register":
    email = st.sidebar.text_input("Email")
    password = st.sidebar.text_input("Password", type="password")
    password2 = st.sidebar.text_input("Confirm Password", type="password")

    if st.sidebar.button("Register"):
        if not email or not password:
            st.sidebar.warning("Enter email and password.")
        elif password != password2:
            st.sidebar.error("Passwords do not match.")
        else:
            try:
                auth_response = supabase.auth.sign_up({"email": email, "password": password})
                if auth_response.user:
                # ✅ only set session state here
                    st.session_state.user = auth_response.user
                else:
                    st.sidebar.error("Registration failed.")
            except Exception as e:
                st.sidebar.error(str(e))

else:  # Login
    email = st.sidebar.text_input("Email")
    password = st.sidebar.text_input("Password", type="password")

    if st.sidebar.button("Login"):
        if not email or not password:
            st.sidebar.warning("Provide email and password.")
        else:
            try:
                auth_response = supabase.auth.sign_in_with_password({"email": email, "password": password})
                if auth_response.user:
                    st.session_state.user = auth_response.user.email
                    st.sidebar.success(f"Logged in as {st.session_state.user}")
                else:
                    st.sidebar.error("Invalid credentials.")
            except Exception as e:
                st.sidebar.error(str(e))

# If not logged in, show a message and stop
if "user" not in st.session_state or not st.session_state.user:
    st.info("You must login via Supabase in the sidebar to access your private data.")
    st.stop()


# ----------------- After login: use per-user master file -----------------
if "user" not in st.session_state:
    st.session_state.user = None

# After successful login:   
#st.session_state.user = auth_response.user
USER_ID = st.session_state.user.id   # unique UUID

if "user" in st.session_state and st.session_state.user:
    user = st.session_state.user
    user_id = user.id   # UUID from Supabase Auth
    username = user.email  # optional if you want display
    MASTER_PATH = user_master_path(user_id) 
    
    master_data = get_or_create_master(user_id, username)
    st.success(f"Loaded master for {username}")




st.sidebar.markdown("---")
st.sidebar.write(f"Signed in as **{USER_ID}**")
st.sidebar.write("Your master file (private to you) will be:")
#st.sidebar.code(MASTER_PATH)

# ----------------- Rest of your app (unchanged logic, but per-user master) -----------------
# ---------- Utilities ----------
def clean_column_names(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = df.columns.astype(str)
    df.columns = df.columns.str.strip()
    if "Name" in df.columns:
        df["Name"] = df["Name"].astype(str).str.strip()
    return df

def extract_week_from_filename(filename: str) -> str:
    match = re.search(r"(Week\s*\d+(?:\s*-\s*[^.]+)?)", filename, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.search(r"week\s*(\d+)", filename, re.IGNORECASE)
    if match:
        return f"Week {int(match.group(1))}"
    return None

def extract_week_num(week_name):
    match = re.search(r"Week\s*(\d+)", str(week_name), re.IGNORECASE)
    return int(match.group(1)) if match else 9999

def get_sorted_week_columns(cols):
    week_columns = [c for c in list(cols) if isinstance(c, str) and re.search(r"Week\s*\d+", c, re.IGNORECASE)]
    return sorted(week_columns, key=extract_week_num)

def compute_all_stats(pivot_df, sorted_week_cols, windows=[2,4]):
    df = pivot_df.copy()
    if len(sorted_week_cols) == 0:
        df["mean_all"] = np.nan
        df["std_all"] = np.nan
        df["cv_all"] = np.nan
        df["count_positive_all"] = 0
        for w in windows:
            df[f"mean_last{w}"] = np.nan
            df[f"std_last{w}"] = np.nan
            df[f"cv_last{w}"] = np.nan
            df[f"count_positive_last{w}"] = 0
        return df

    numeric_all = df[sorted_week_cols].apply(pd.to_numeric, errors="coerce")
    df["mean_all"] = numeric_all.mean(axis=1)
    df["std_all"] = numeric_all.std(axis=1)
    mean_safe = df["mean_all"].replace({0: np.nan})
    df["cv_all"] = df["std_all"] / mean_safe
    df["count_positive_all"] = (numeric_all > 0).sum(axis=1)

    for w in windows:
        last_cols = sorted_week_cols[-w:] if len(sorted_week_cols) >= 1 else []
        if len(last_cols) == 0:
            df[f"mean_last{w}"] = np.nan
            df[f"std_last{w}"] = np.nan
            df[f"cv_last{w}"] = np.nan
            df[f"count_positive_last{w}"] = 0
        else:
            dd = df[last_cols].apply(pd.to_numeric, errors="coerce")
            df[f"mean_last{w}"] = dd.mean(axis=1)
            df[f"std_last{w}"] = dd.std(axis=1)
            mean_safe2 = df[f"mean_last{w}"].replace({0: np.nan})
            df[f"cv_last{w}"] = df[f"std_last{w}"] / mean_safe2
            df[f"count_positive_last{w}"] = (dd > 0).sum(axis=1)
    return df

# robust numeric cleaner for filters
def clean_and_coerce_numeric(series: pd.Series):
    s = series.astype(str).fillna("").str.strip()
    s = s.str.replace(r"[,\s₹]", "", regex=True)
    s = s.str.replace(r"(?i)\bcr\b|\bcr\.\b|\bmn\b|\bmillion\b|\blakh\b|\blac\b", "", regex=True)
    s = s.str.replace(r"^\((.*)\)$", r"-\1", regex=True)
    is_percent = s.str.contains(r"%$")
    s = s.str.replace(r"%$", "", regex=True)
    s = s.replace({"": np.nan, "—": np.nan, "na": np.nan, "n/a": np.nan, "None": np.nan, "NoneType": np.nan})
    s = s.str.replace(r"[^\d\.\-eE]", "", regex=True)
    coerced = pd.to_numeric(s, errors="coerce")
    try:
        coerced.loc[is_percent] = coerced.loc[is_percent] / 100.0
    except Exception:
        pass
    return coerced

# ---------- Per-user master load/save ----------
def load_master_for_user(path):
    if os.path.exists(path):
        try:
            m = pd.read_excel(path)
            return clean_column_names(m)
        except Exception as e:
            st.error(f"Failed to read your master file: {e}")
            return pd.DataFrame()
    return pd.DataFrame()

def save_master_for_user(df, path):
    try:
        df.to_excel(path, index=False)
        print(f"✅ Master saved at {path} with shape {df.shape}")
    except Exception as e:
        print(f"❌ Failed to save master: {e}")

def make_pivot_from_master(master_df: pd.DataFrame):
    # Require Name, Week, and Return over 1week
    if "Name" not in master_df.columns or "Week" not in master_df.columns or "Return over 1week" not in master_df.columns:
        return pd.DataFrame()

    # Ensure numeric for returns
    master_df["Return over 1week"] = clean_and_coerce_numeric(master_df["Return over 1week"])

    # Pivot: each Name × Week → Return value
    pivot = master_df.pivot_table(
        index="Name",
        columns="Week",
        values="Return over 1week",
        aggfunc="first"
    ).reset_index()

    return clean_column_names(pivot)

# ---------- App functionality (same as before) ----------
st.markdown("---")
st.header("Upload weekly Excel file(s)")
st.markdown("Upload one or more weekly Excel files. Each user's uploads are stored privately in their own master file.")

uploaded_files = st.file_uploader("Upload .xlsx files", type=["xlsx"], accept_multiple_files=True)
forced_week_label = st.text_input("Force Week label for uploads (optional)", value="")

# Load the user's master (private)
master_df = load_master_for_user(MASTER_PATH)

if st.button("Process Uploads"):
    if not uploaded_files:
        st.warning("Please upload at least one .xlsx file.")
    else:
        new_rows = []
        replaced_weeks = []
        for uploaded in uploaded_files:
            try:
                # --- Load Excel robustly (all sheets) ---
                dfs = pd.read_excel(uploaded, sheet_name=None)  # dict of {sheet_name: DataFrame}
                df = None
                for sheetname, sheetdf in dfs.items():
                    sheetdf = clean_column_names(sheetdf)
                    if "Name" in sheetdf.columns:   # pick the sheet with stock names
                        df = sheetdf
                        break
                if df is None:
                    st.error(f"No valid sheet with 'Name' column found in {uploaded.name}")
                    continue

                # --- Assign week label ---
                filename = uploaded.name
                week_label = extract_week_from_filename(filename)
                if forced_week_label.strip():
                    week_label = forced_week_label.strip()
                if week_label is None:
                    week_label = os.path.splitext(filename)[0]
                df["Week"] = week_label

                # --- Replace existing week if already present ---
                if not master_df.empty and "Week" in master_df.columns:
                    if week_label in master_df["Week"].values:
                        master_df = master_df[master_df["Week"] != week_label]
                        replaced_weeks.append(week_label)

                new_rows.append(df)
                st.success(f"Accepted {filename} as {week_label} ({len(df)} rows).")

            except Exception as e:
                st.error(f"Failed to process {uploaded.name}: {e}")

        if new_rows:
            appended = pd.concat(new_rows, ignore_index=True)
            master_df = pd.concat([master_df, appended], ignore_index=True) if not master_df.empty else appended
            master_df = clean_column_names(master_df)

            # --- Debug before saving ---
            st.write("Saving master with shape:", master_df.shape)
            st.dataframe(master_df.head())

            save_master_for_user(master_df, MASTER_PATH)

            # --- Debug file existence ---
            st.write("Saved master file:", MASTER_PATH, "Exists:", os.path.exists(MASTER_PATH))

            st.success(
                f"Your master updated. Replaced weeks: {replaced_weeks}" if replaced_weeks else "Master updated (new weeks appended)."
            )
            st.rerun()

# ----------------- After reload -----------------
st.header("Raw Data")

# --- Debug after reload ---
st.write("Loaded master path:", MASTER_PATH)
st.write("Loaded master shape:", master_df.shape)
if not master_df.empty:
    st.dataframe(master_df.head())
st.header("Stats from your private master")

if master_df.empty:
    st.info("You have no data yet. Upload weekly Excel files to begin.")
else:
    # Detect fundamentals (same list)
    desired_fundamentals = [
        "Industry", "Market Capitalization", "YOY Quarterly profit growth",
        "YOY Quarterly sales growth", "QoQ Profits", "Profit growth 3Years",
        "Sales growth 3Years", "Return on capital employed", "Return on equity",
        "Price to Earning", "Sales latest quarter", "PEG Ratio"
    ]
    fundamental_cols = [c for c in desired_fundamentals if c in master_df.columns]

    pivot_df = make_pivot_from_master(master_df)
    
    
    if pivot_df.empty:
        st.error("Pivot creation failed — check your uploaded files contain 'Return over 1week' and 'Week' columns.")
    else:
        sorted_week_cols = get_sorted_week_columns(pivot_df.columns)
        if "Name" not in pivot_df.columns:
            st.error("'Name' not present in pivot - cannot proceed.")
        else:
            pivot_df = pivot_df[["Name"] + sorted_week_cols]
            pivot_with_stats = compute_all_stats(pivot_df, sorted_week_cols, windows=[2,4])
            fundamentals = master_df.groupby("Name")[fundamental_cols].first().reset_index() if fundamental_cols else pd.DataFrame({"Name": pivot_with_stats["Name"]})
            final_df = pd.merge(fundamentals, pivot_with_stats, on="Name", how="left")

            # Sidebar filters (minimum-only numeric as you asked)
            st.sidebar.markdown("## Filters (Your fundamentals)")
            st.sidebar.write("Filters apply only to your data (private).")
            filtered_df = final_df.copy()

            if len(fundamental_cols) > 0:
                for col in fundamental_cols:
                    if col not in final_df.columns:
                        continue
                    series = final_df[col]
                    coerced = clean_and_coerce_numeric(series)
                    num_non_na = coerced.dropna().shape[0]
                    unique_values = series.dropna().unique()
                    unique_count = len(unique_values)

                    if num_non_na > 0:
                        col_min = float(np.nanmin(coerced))
                        col_max = float(np.nanmax(coerced))
                        label = f"{col} minimum (≥). available range: [{col_min}, {col_max}]"
                        min_input = st.sidebar.number_input(label, value=col_min, format="%.6f")
                        masked_vals = clean_and_coerce_numeric(filtered_df[col])
                        mask = masked_vals >= float(min_input)
                        filtered_df = filtered_df[mask.fillna(False)]
                    else:
                        if unique_count <= 50:
                            options = sorted([str(x) for x in unique_values if pd.notna(x)])
                            chosen = st.sidebar.multiselect(f"{col} (select)", options=options, default=options)
                            if chosen:
                                filtered_df = filtered_df[filtered_df[col].astype(str).isin(chosen)]
                        else:
                            txt = st.sidebar.text_input(f"{col} contains (text search)")
                            if txt and txt.strip():
                                filtered_df = filtered_df[filtered_df[col].astype(str).str.contains(txt.strip(), case=False, na=False)]
            else:
                st.sidebar.write("No fundamental columns detected to filter.")

            if st.sidebar.button("Reset filters"):
                st.experimental_rerun()

            st.write(f"Filtered rows (private): {len(filtered_df)}")
            st.dataframe(filtered_df.head(100))

            # Column selection and download (private)
            display_df = filtered_df.copy()
            all_cols = display_df.columns.tolist()
            st.write(f"Columns available: {len(all_cols)}")
            with st.expander("Choose columns to include in the download (optional)"):
                selected_cols = st.multiselect("Select columns", options=all_cols, default=all_cols)
                download_df = display_df[selected_cols].copy() if selected_cols else display_df.copy()
            if "selected_cols" not in locals():
                download_df = display_df.copy()

            today = datetime.now().strftime("%d-%m-%Y")
            unique_inds = download_df["Industry"].dropna().unique().tolist() if "Industry" in download_df.columns else []
            ind_for_name = unique_inds[0] if len(unique_inds) == 1 else "all-industries"
            out_filename = f"{today}_{USERNAME}_{ind_for_name}_returns_with_stats.xlsx"

            # Save per-user final master copy (optional)
            try:
                atomic_save_excel(final_df, MASTER_PATH.replace(".xlsx", "_final.xlsx"))
            except Exception:
                pass

            towrite = io.BytesIO()
            with pd.ExcelWriter(towrite, engine="openpyxl") as writer:
                download_df.to_excel(writer, index=False, sheet_name="ReturnsWithStats")
                master_df.to_excel(writer, index=False, sheet_name="MasterRaw")
                pivot_with_stats.to_excel(writer, index=False, sheet_name="PivotStats")
            towrite.seek(0)

            st.download_button(
                label=f"Download final Excel (private)",
                data=towrite.getvalue(),
                file_name=out_filename,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

            st.success("Generated your private Excel. Only you (your username) can access this master file on the server.")

# Footer
st.markdown("---")
st.caption("Note: This local auth system stores salted SHA-256 hashes in users.json. For production, use OAuth/providers and DB storage.")
