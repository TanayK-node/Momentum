# app.py (properly indented & fixed)
import streamlit as st
import pandas as pd
import numpy as np
import glob
import re
import io
import os
from datetime import datetime
import xlsxwriter

st.set_page_config(page_title="Momentum Weekly Uploader", layout="wide")

st.title("Momentum Investing — Weekly Upload + Stats")
st.markdown(
    "Upload weekly Excel(s). The app keeps a master dataset, replaces any existing week with the same Week name, "
    "recomputes pivot + stats (all, last 4, last 2) and lets you download the final Excel."
)

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

def load_master(path="master_data.xlsx"):
    if os.path.exists(path):
        try:
            m = pd.read_excel(path)
            return m
        except Exception as e:
            st.error(f"Failed to read existing master file: {e}")
            return pd.DataFrame()
    return pd.DataFrame()

def save_master(df, path="master_data.xlsx"):
    try:
        df.to_excel(path, index=False)
    except Exception as e:
        st.error(f"Failed to save master file: {e}")

def make_pivot_from_master(master_df):
    master_df = clean_column_names(master_df)
    if "Week" not in master_df.columns:
        st.error("No 'Week' column found in master data.")
        return pd.DataFrame()
    if "Return over 1week" not in master_df.columns:
        st.error("No 'Return over 1week' column found in master data.")
        return pd.DataFrame()
    pivot = master_df.pivot_table(index="Name", columns="Week", values="Return over 1week").reset_index()
    return pivot

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
        # if index misaligns, ignore percent conversion gracefully
        pass
    return coerced

# ---------- App state / storage ----------
MASTER_PATH = "master_data.xlsx"
master_df = load_master(MASTER_PATH)
if not master_df.empty:
    master_df = clean_column_names(master_df)

st.sidebar.header("Master dataset")
st.sidebar.write(f"Master file present: {'Yes' if not master_df.empty else 'No'}")
if not master_df.empty:
    st.sidebar.write(f"Rows in master: {len(master_df)}")
    unique_weeks = sorted(master_df["Week"].dropna().unique(), key=extract_week_num) if "Week" in master_df.columns else []
    st.sidebar.write(unique_weeks[:10])

# ---------- Upload UI ----------
st.header("Upload weekly Excel file(s)")
st.markdown(
    "You can upload one or multiple Excel files. "
    "If the app can detect the Week name from the filename it will use it; otherwise you can type the Week label below."
)
uploaded_files = st.file_uploader("Upload .xlsx files", type=["xlsx"], accept_multiple_files=True)
forced_week_label = st.text_input("If filename lacks Week label or you want to force a Week name for all uploads, enter it here (e.g. 'Week 17 - 23 Aug')", value="")

if st.button("Process Uploads"):
    if not uploaded_files:
        st.warning("Please upload at least one .xlsx file.")
    else:
        new_rows = []
        replaced_weeks = []
        for uploaded in uploaded_files:
            try:
                df = pd.read_excel(uploaded)
                df = clean_column_names(df)
                filename = uploaded.name
                week_label = extract_week_from_filename(filename)
                if forced_week_label.strip():
                    week_label = forced_week_label.strip()
                if week_label is None:
                    week_label = os.path.splitext(filename)[0]
                df["Week"] = week_label
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
            save_master(master_df, MASTER_PATH)
            st.success(f"Master updated. Replaced weeks: {replaced_weeks}" if replaced_weeks else "Master updated (new weeks appended).")
            try:
                st.rerun()
            except Exception:
                try:
                    st.experimental_rerun()
                except Exception:
                    pass

# ---------- Main processing ----------
st.header("Stats from master data")

if master_df.empty:
    st.info("No master data available yet. Upload weekly Excel files to begin.")
else:
    desired_fundamentals = [
        "Industry", "Market Capitalization", "YOY Quarterly profit growth",
        "YOY Quarterly sales growth", "QoQ Profits", "Profit growth 3Years",
        "Sales growth 3Years", "Return on capital employed", "Return on equity",
        "Price to Earning", "Sales latest quarter", "PEG Ratio"
    ]
    fundamental_cols = [c for c in desired_fundamentals if c in master_df.columns]

    pivot_df = make_pivot_from_master(master_df)
    if pivot_df.empty:
        st.error("Pivot creation failed — check 'Return over 1week' and 'Week' columns exist.")
    else:
        sorted_week_cols = get_sorted_week_columns(pivot_df.columns)
        if "Name" not in pivot_df.columns:
            st.error("'Name' not present in pivot - cannot proceed.")
        else:
            pivot_df = pivot_df[["Name"] + sorted_week_cols]
            pivot_with_stats = compute_all_stats(pivot_df, sorted_week_cols, windows=[2,4])
            fundamentals = master_df.groupby("Name")[fundamental_cols].first().reset_index() if fundamental_cols else pd.DataFrame({"Name": pivot_with_stats["Name"]})
            final_df = pd.merge(fundamentals, pivot_with_stats, on="Name", how="left")

            #st.subheader("Pivot + Stats (sample)")

            # ---------- Sidebar filters for fundamentals ----------
            st.sidebar.markdown("## Filters (Fundamentals)")
            st.sidebar.write("Use these filters to narrow companies before viewing/downloading.")
            filtered_df = final_df.copy()

            # If fundamental_cols exists and has items, create filters for them
            # If fundamental_cols exists and has items, create filters for them
            if 'fundamental_cols' in locals() and len(fundamental_cols) > 0:
                for col in fundamental_cols:
                    if col not in final_df.columns:
                        continue

                    series = final_df[col]
                    coerced = clean_and_coerce_numeric(series)
                    num_non_na = coerced.dropna().shape[0]
                    unique_values = series.dropna().unique()
                    unique_count = len(unique_values)

                    # NUMERIC: show a minimum-only input (filter value >= min_input)
                    if num_non_na > 0:
                        col_min = float(np.nanmin(coerced))
                        col_max = float(np.nanmax(coerced))
                        # Display helpful label with sample range
                        label = f"{col} minimum (≥). available range: [{col_min}, {col_max}]"
                        # Default to the minimum observed value
                        min_input = st.sidebar.number_input(label, value=col_min, format="%.6f")
                        # Apply filter using cleaned numeric values
                        masked_vals = clean_and_coerce_numeric(filtered_df[col])
                        mask = masked_vals >= float(min_input)
                        filtered_df = filtered_df[mask.fillna(False)]
                    else:
                        # CATEGORICAL / TEXT fallback (unchanged)
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

            # Reset filters button
            if st.sidebar.button("Reset filters"):
                try:
                    st.rerun()
                except Exception:
                    try:
                        st.experimental_rerun()
                    except Exception:
                        pass

            st.write(f"Filtered rows: {len(filtered_df)} (after applying sidebar filters)")
            st.dataframe(filtered_df.head(100))

            display_df = filtered_df.copy()

            # Column selection and download
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
            out_filename = f"{today}_{ind_for_name}_returns_with_stats.xlsx"

            try:
                final_df.to_excel("returns_with_stats.xlsx", index=False)
            except Exception as e:
                st.warning(f"Couldn't save returns_with_stats.xlsx locally: {e}")

            towrite = io.BytesIO()
            with pd.ExcelWriter(towrite, engine="xlsxwriter") as writer:
                try:
                    download_df.to_excel(writer, index=False, sheet_name="ReturnsWithStats")
                    master_df.to_excel(writer, index=False, sheet_name="MasterRaw")
                    pivot_with_stats.to_excel(writer, index=False, sheet_name="PivotStats")
                except Exception as e:
                    st.error(f"Failed to write sheets to Excel: {e}")
            towrite.seek(0)

            st.download_button(
                label=f"Download final Excel",
                data=towrite.getvalue(),
                file_name=out_filename,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

            st.success("Final Excel generated. 'returns_with_stats.xlsx' saved on server (if running locally).")

# ---------- Footer / tips ----------
st.markdown("---")
st.markdown(
    "Notes:\n\n"
    "- Uploaded files must contain a `Return over 1week` column and company `Name` column.\n"
    "- If the uploaded filename contains a Week label (e.g. 'Week 5 - 31 May.xlsx'), the app will use that Week name. "
    "You can also force a Week label for uploads using the text box above.\n"
    "- When you upload a file for a Week already present in the master, the app replaces the old records for that Week (so the master always reflects the latest uploaded Excel for a Week).\n"
    "- The app automatically includes the fundamentals listed in the UI if they exist in your master file. "
)
st.markdown("Run with: `streamlit run app.py`")
