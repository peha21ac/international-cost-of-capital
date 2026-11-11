"""Cost of equity and WACC: USD vs. the currency of a selected country.

Single-file Streamlit app (Python 3.11, Streamlit Community Cloud ready).

Layout of this file
-------------------
1. Constants and an embedded fallback tax table
2. ``compute_wacc``  - the pure calculation (no Streamlit, unit-testable)
3. Data fetchers     - Damodaran betas, OECD tax rates, Damodaran spread,
                       Trading Economics yields (each fails gracefully)
4. Helpers           - name matching, formatting
5. ``main``          - the Streamlit user interface
"""

from __future__ import annotations

import io
import re
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st

# ---------------------------------------------------------------------------
# 1. Constants
# ---------------------------------------------------------------------------

DEFAULT_INDUSTRY = "Software (System & Application)"
DEFAULT_COUNTRY = "United States"

URL_BETAS = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
URL_OECD = (
    "https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/"
    "full-report/statutory-corporate-income-tax-rates_440f9286.html"
)
URL_RATINGS = "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html"
URL_RATINGS_ALT = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html"
URL_BONDS = "https://tradingeconomics.com/bonds"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Approximate OECD combined statutory CIT rates (%), used ONLY if scraping fails.
# Values are rounded 2024 figures entered from memory of the OECD table; the app
# shows a visible warning whenever they are used.
FALLBACK_TAX = {
    "Argentina": 35.0, "Australia": 30.0, "Austria": 23.0, "Belgium": 25.0,
    "Brazil": 34.0, "Bulgaria": 10.0, "Canada": 26.2, "Chile": 27.0,
    "China": 25.0, "Colombia": 35.0, "Costa Rica": 30.0, "Croatia": 18.0,
    "Cyprus": 12.5, "Czechia": 21.0, "Denmark": 22.0, "Estonia": 22.0,
    "Finland": 20.0, "France": 25.8, "Germany": 29.9, "Greece": 22.0,
    "Hungary": 9.0, "Iceland": 20.0, "India": 25.2, "Indonesia": 22.0,
    "Ireland": 12.5, "Israel": 23.0, "Italy": 27.8, "Japan": 29.7,
    "Korea": 26.4, "Latvia": 20.0, "Lithuania": 15.0, "Luxembourg": 24.9,
    "Malta": 35.0, "Mexico": 30.0, "Netherlands": 25.8, "New Zealand": 28.0,
    "Norway": 22.0, "Peru": 29.5, "Poland": 19.0, "Portugal": 31.5,
    "Romania": 16.0, "Saudi Arabia": 20.0, "Singapore": 17.0,
    "Slovak Republic": 21.0, "Slovenia": 22.0, "South Africa": 27.0,
    "Spain": 25.0, "Sweden": 20.6, "Switzerland": 19.6, "Türkiye": 25.0,
    "United Kingdom": 25.0, "United States": 25.77,
}

# OECD name -> Trading Economics name, where they differ.
TE_ALIASES = {
    "Korea": ["South Korea", "Korea"],
    "Türkiye": ["Turkey", "Türkiye", "Turkiye"],
    "Slovak Republic": ["Slovakia", "Slovak Republic"],
    "Czechia": ["Czech Republic", "Czechia"],
    "United States": ["United States", "US", "USA"],
    "United Kingdom": ["United Kingdom", "UK", "Great Britain"],
    "Russia": ["Russia", "Russian Federation"],
    "Viet Nam": ["Vietnam", "Viet Nam"],
    "Hong Kong (China)": ["Hong Kong"],
    "Hong Kong, China": ["Hong Kong"],
    "People's Republic of China": ["China"],
    "Republic of Korea": ["South Korea"],
}

MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|"
    "November|December"
)


# ---------------------------------------------------------------------------
# 2. Pure calculation (no Streamlit calls)
# ---------------------------------------------------------------------------

def compute_wacc(
    beta_u: float,
    d: float,
    tau: float,
    erp: float,
    rf: float,
    rf_f: float,
    s: float,
    s_f: float | None = None,
) -> dict:
    """Compute cost of equity and WACC in USD and in a foreign currency.

    All rates are DECIMALS (0.065 for 6.5 %). Full precision is kept; callers
    round for display only.

    Parameters
    ----------
    beta_u : unlevered (asset) beta, debt beta assumed zero.
    d      : target debt share D/(D+E), 0 <= d < 1.
    tau    : project tax rate, used identically in both currencies.
    erp    : equity risk premium over the U.S. risk-free rate.
    rf     : U.S. 10-year yield (USD risk-free proxy).
    rf_f   : foreign 10-year yield (foreign-currency risk-free proxy).
    s      : USD borrowing spread over the U.S. 10-year yield.
    s_f    : foreign borrowing spread; if None, s* = s * F is used.

    Returns
    -------
    dict with inputs echoed plus: de, beta_e, re, rd, rd_after, wacc,
    factor, re_f, s_f, rd_f, rd_f_after, wacc_f, s_f_auto.
    """
    if not 0.0 <= d < 1.0:
        raise ValueError("Debt share d must satisfy 0 <= d < 1 (d = 100% is undefined).")
    if 1.0 + rf <= 0.0:
        raise ValueError("U.S. yield must be greater than -100%.")
    if 1.0 + rf_f <= 0.0:
        raise ValueError("Foreign yield must be greater than -100%.")

    de = d / (1.0 - d)                                  # D/E
    beta_e = beta_u * (1.0 + (1.0 - tau) * de)          # Hamada relevering

    # --- USD ---
    re = rf + beta_e * erp                              # CAPM
    rd = rf + s                                         # pretax cost of debt
    rd_after = rd * (1.0 - tau)
    wacc = (1.0 - d) * re + d * rd_after

    # --- Foreign currency (equal real rates + relative PPP) ---
    factor = (1.0 + rf_f) / (1.0 + rf)                  # F
    # (1+Re)F - 1, written as Re + (1+Re)(F-1) so that F = 1 returns Re exactly
    re_f = re + (1.0 + re) * (factor - 1.0)
    s_f_auto = s * factor
    s_f_used = s_f_auto if s_f is None else s_f
    rd_f = rf_f + s_f_used
    rd_f_after = rd_f * (1.0 - tau)
    wacc_f = (1.0 - d) * re_f + d * rd_f_after

    return {
        "beta_u": beta_u, "d": d, "tau": tau, "erp": erp, "rf": rf,
        "rf_f": rf_f, "s": s, "de": de, "beta_e": beta_e,
        "re": re, "rd": rd, "rd_after": rd_after, "wacc": wacc,
        "factor": factor, "re_f": re_f, "s_f": s_f_used, "s_f_auto": s_f_auto,
        "rd_f": rd_f, "rd_f_after": rd_f_after, "wacc_f": wacc_f,
    }


# ---------------------------------------------------------------------------
# 3. Data fetchers
#    Inner functions raise on failure (so failures are never cached);
#    the safe_* wrappers turn exceptions into {"ok": False, "error": ...}.
# ---------------------------------------------------------------------------

def _get(url: str) -> requests.Response:
    """HTTP GET with a browser User-Agent and a timeout."""
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _find_month_year(text: str) -> str | None:
    m = re.search(rf"({MONTHS})\s+(\d{{4}})", text)
    return f"{m.group(1)} {m.group(2)}" if m else None


def _to_number(x) -> float | None:
    """First number found in a cell (handles '25.8', '25.8 a', '1.11%')."""
    if x is None:
        return None
    if isinstance(x, (int, float)) and not pd.isna(x):
        return float(x)
    m = re.search(r"-?\d+(?:\.\d+)?", str(x).replace(",", ""))
    return float(m.group()) if m else None


@st.cache_data(ttl=6 * 3600, show_spinner="Fetching Damodaran betas...")
def _fetch_betas() -> dict:
    resp = _get(URL_BETAS)
    raw = None
    for engine in ("xlrd", "openpyxl"):  # the .xls may really be an .xlsx
        try:
            raw = pd.read_excel(io.BytesIO(resp.content), sheet_name=None,
                                header=None, engine=engine)
            break
        except Exception:
            continue
    if raw is None:
        raise ValueError("Could not open betas.xls with xlrd or openpyxl.")

    # Prefer the 'Industry Averages' sheet, else any sheet with an 'Industry Name' header.
    names = sorted(raw, key=lambda n: 0 if "industry" in str(n).lower() else 1)
    table, asof = None, None
    for name in names:
        df = raw[name]
        for i in range(min(40, len(df))):
            row = [str(v).strip().lower() for v in df.iloc[i].tolist()]
            if "industry name" in row:
                head = [str(v).strip() for v in df.iloc[i].tolist()]
                body = df.iloc[i + 1:].copy()
                body.columns = head
                table = body
                for j in range(i):  # data date is in the rows above the header
                    for v in df.iloc[j].tolist():
                        asof = asof or _find_month_year(str(v))
                break
        if table is not None:
            break
    if table is None:
        raise ValueError("No 'Industry Name' header row found in betas.xls.")

    cols = {c.lower(): c for c in table.columns}
    col = cols.get("unlevered beta")
    if col is None:  # tolerate small renames, but never the cash-corrected column
        for low, orig in cols.items():
            if low.startswith("unlevered beta") and "cash" not in low:
                col = orig
                break
    if col is None:
        raise ValueError(f"'Unlevered beta' column not found. Columns: {list(table.columns)}")

    name_col = cols["industry name"]
    out = table[[name_col, col]].copy()
    out.columns = ["industry", "beta_u"]
    out["beta_u"] = pd.to_numeric(out["beta_u"], errors="coerce")
    out = out.dropna()
    out["industry"] = out["industry"].astype(str).str.strip()
    out = out[~out["industry"].str.lower().str.startswith("total market")]
    if out.empty:
        raise ValueError("Beta table parsed but empty.")

    if not asof:
        asof = "file date " + resp.headers.get("Last-Modified", "not stated")
    return {"betas": dict(zip(out["industry"], out["beta_u"])), "asof": asof}


@st.cache_data(ttl=6 * 3600, show_spinner="Fetching OECD tax rates...")
def _fetch_tax() -> dict:
    html = _get(URL_OECD).text
    tables = pd.read_html(io.StringIO(html))
    best, best_year, best_n = None, None, 0
    for t in tables:
        t = t.copy()
        t.columns = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in t.columns]
        years = [c for c in t.columns if re.fullmatch(r"\s*(19|20)\d\d\s*", c)]
        if len(t) < 20 or not years:
            continue
        years.sort(key=lambda c: int(c.strip()))
        # latest year column that has a reasonable number of values
        for ycol in reversed(years):
            n = t[ycol].map(_to_number).notna().sum()
            if n >= 20:
                if n > best_n:
                    best, best_year, best_n = (t, ycol, n)
                break
    if best is None:
        raise ValueError("No OECD table with country rows and year columns found.")
    country_col = best.columns[0]
    rates = {}
    for _, r in best.iterrows():
        name = re.sub(r"\s*\d+$|\s*\(.*?\)$", "", str(r[country_col])).strip()
        val = _to_number(r[best_year])
        if name and val is not None and 0 <= val <= 60 and name.lower() != "nan":
            rates[name] = val
    if len(rates) < 20:
        raise ValueError("OECD table parsed but too few countries.")
    return {"rates": rates, "asof": f"{best_year.strip()} (statutory rates)"}


@st.cache_data(ttl=6 * 3600, show_spinner="Fetching Damodaran ratings spread...")
def _fetch_spread() -> dict:
    last_err = None
    for url in (URL_RATINGS, URL_RATINGS_ALT):
        try:
            html = _get(url).text
            break
        except Exception as e:  # try the alternate host path
            last_err = e
    else:
        raise last_err  # type: ignore[misc]
    tables = pd.read_html(io.StringIO(html))
    spread = None
    for t in tables:  # the first table containing Baa2/BBB is the large-firm table
        for _, row in t.iterrows():
            cells = [str(v) for v in row.tolist()]
            if any(re.fullmatch(r"\s*Baa2\s*/\s*BBB\s*", c) for c in cells):
                nums = [_to_number(c) for c in cells if "%" in c]
                # last '%' cell in the row is the spread (earlier ones are other columns)
                if nums:
                    spread = nums[-1]
                break
        if spread is not None:
            break
    if spread is None:
        raise ValueError("Baa2/BBB row not found on the ratings page.")
    asof = _find_month_year(re.sub(r"<[^>]+>", " ", html)) or f"retrieved {_now()}"
    return {"spread": spread, "asof": asof}


@st.cache_data(ttl=3600, show_spinner="Fetching 10-year yields...")
def _fetch_yields() -> dict:
    html = _get(URL_BONDS).text
    tables = pd.read_html(io.StringIO(html))
    yields = {}
    for t in tables:
        cols = [str(c) for c in t.columns]
        ycol = next((c for c in cols if c.strip().lower() == "yield"), None)
        if ycol is None or len(cols) < 2:
            continue
        ccol = cols[0]
        t.columns = cols
        for _, r in t.iterrows():
            name = str(r[ccol]).strip()
            val = _to_number(r[ycol])
            if name and val is not None:
                yields[_norm(name)] = val
    if "unitedstates" not in yields:
        raise ValueError("U.S. row not found in the Trading Economics bond table.")
    return {"yields": yields, "asof": f"retrieved {_now()}"}


def _safe(fn):
    """Run a cached fetcher; return ({data}, None) or (None, error message)."""
    try:
        return fn(), None
    except Exception as e:  # network, parsing, anything: never crash the app
        return None, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# 4. Helpers
# ---------------------------------------------------------------------------

def _norm(name: str) -> str:
    """Normalise a country name for matching (lowercase, alphanumerics only)."""
    return re.sub(r"[^a-z0-9]", "", str(name).lower().replace("ü", "u"))


def find_yield(yields: dict | None, country: str) -> float | None:
    """Look up a country's 10y yield (%) allowing for name mismatches."""
    if not yields:
        return None
    for cand in [country] + TE_ALIASES.get(country, []):
        if _norm(cand) in yields:
            return yields[_norm(cand)]
    return None


def pct(x: float, nd: int = 3) -> str:
    return f"{x * 100:.{nd}f}%"


def source_line(name: str, url: str, asof: str) -> str:
    return f"Source: [{name}]({url}) · as of {asof}"


# ---------------------------------------------------------------------------
# 5. Streamlit UI
# ---------------------------------------------------------------------------

def override_input(label, key, fetched, src, unit, step, default_if_missing, fmt, force_off=False):
    """Checkbox + number input pair. Returns (value, is_overridden).

    Value is in the display unit (e.g. percent). If nothing was fetched, the
    override is forced on so the user must type a number.
    """
    missing = fetched is None
    if force_off:
        st.caption(f"{label}: **{fetched:{fmt}}{unit}** (U.S. selected: F = 1)")
        return fetched, False
    on = st.checkbox(f"Override {label}", value=missing, disabled=missing, key=f"ov_{key}")
    on = on or missing
    if on:
        start = default_if_missing if missing else fetched
        if missing:
            st.warning(f"{label} could not be fetched - enter it manually.")
        val = st.number_input(label + (f" ({unit.strip()})" if unit.strip() else ""), value=float(start),
                              step=step, format="%" + fmt,
                              key=f"in_{key}_{round(start, 6)}")
        return val, True
    st.caption(f"{label}: **{fetched:{fmt}}{unit}**  \n{src}")
    return fetched, False


def main() -> None:
    st.set_page_config(page_title="Cost of capital: USD vs local currency", layout="wide")
    st.title("Cost of equity and WACC: USD vs. selected currency")

    top = st.columns([1, 5])
    if top[0].button("Refresh source data"):
        st.cache_data.clear()
        st.rerun()

    # ---- fetch all sources (cached) ----
    beta_d, beta_err = _safe(_fetch_betas)
    tax_d, tax_err = _safe(_fetch_tax)
    spr_d, spr_err = _safe(_fetch_spread)
    yld_d, yld_err = _safe(_fetch_yields)

    for name, err in (("Damodaran unlevered betas", beta_err), ("OECD tax rates", tax_err),
                      ("Damodaran BBB spread", spr_err), ("Trading Economics yields", yld_err)):
        if err:
            st.warning(f"{name} could not be fetched ({err}). Manual input is required for "
                       "anything that depends on it.")

    using_fallback_tax = tax_d is None
    if using_fallback_tax:
        tax_d = {"rates": FALLBACK_TAX, "asof": "embedded approximate 2024 table"}
        st.warning("OECD scrape failed: using an **embedded, approximate** tax table. "
                   "Check the rate or override it in the sidebar.")

    betas = beta_d["betas"] if beta_d else {}
    industries = sorted(betas) if betas else [DEFAULT_INDUSTRY]
    countries = sorted(tax_d["rates"])
    if DEFAULT_COUNTRY not in countries:
        countries.insert(0, DEFAULT_COUNTRY)

    # ---- sidebar ----
    sb = st.sidebar
    sb.header("Inputs")
    industry = sb.selectbox("Industry", industries,
                            index=industries.index(DEFAULT_INDUSTRY) if DEFAULT_INDUSTRY in industries else 0)
    country = sb.selectbox("Project country / currency", countries,
                           index=countries.index(DEFAULT_COUNTRY))
    erp_pct = sb.number_input("Equity risk premium (%)", value=6.5, step=0.1, format="%.2f")
    d_pct = sb.slider("Target debt share d = D/(D+E) (%)", 0, 100, 30)
    tax_choice = sb.radio("Project tax rate", ["U.S. tax rate", "Selected-country tax rate"], index=0)
    sb.caption("The chosen rate is the same project tax rate in both currency calculations.")
    is_us = country == DEFAULT_COUNTRY

    sb.subheader("Source values and overrides")
    with sb:
        src_b = source_line("Damodaran betas", URL_BETAS, beta_d["asof"]) if beta_d else ""
        beta_u, ov_beta = override_input("Unlevered beta", "beta", betas.get(industry), src_b,
                                         "", 0.01, 1.0, ".4f")

        rates = tax_d["rates"]
        tax_name = "United States" if tax_choice.startswith("U.S.") else country
        tax_fetched = rates.get(tax_name)
        src_t = source_line("OECD", URL_OECD, tax_d["asof"])
        tax_pct, ov_tax = override_input(f"Corporate tax rate ({tax_name})", "tax", tax_fetched,
                                         src_t, " %", 0.1, 25.0, ".2f")

        src_s = source_line("Damodaran ratings (Baa2/BBB)", URL_RATINGS, spr_d["asof"]) if spr_d else ""
        s_pct, ov_s = override_input("USD BBB spread", "spread", spr_d["spread"] if spr_d else None,
                                     src_s, " %", 0.05, 1.5, ".2f")

        ys = yld_d["yields"] if yld_d else None
        src_y = source_line("Trading Economics", URL_BONDS, yld_d["asof"]) if yld_d else ""
        rf_pct, ov_rf = override_input("U.S. 10-year yield", "rf", find_yield(ys, "United States"),
                                       src_y, " %", 0.05, 4.0, ".3f")
        rff_fetched = rf_pct if is_us else find_yield(ys, country)
        if not is_us and ys and rff_fetched is None:
            st.warning(f"No yield found for {country} in the Trading Economics table.")
        rff_pct, ov_rff = override_input(f"{country} 10-year yield", "rff", rff_fetched, src_y,
                                         " %", 0.05, 4.0, ".3f", force_off=is_us)
        if is_us:
            rff_pct = rf_pct  # F = 1 exactly, even if the U.S. yield is overridden

    # ---- compute ----
    d = d_pct / 100
    if d >= 1.0:
        st.error("Debt share of 100% is undefined (D/E is infinite). Choose d below 100%.")
        st.stop()
    rf, rff, tau, s, erp = rf_pct / 100, rff_pct / 100, tax_pct / 100, s_pct / 100, erp_pct / 100
    factor = (1 + rff) / (1 + rf)
    s_auto = s * factor
    with sb:
        ov_sf = sb.checkbox("Override foreign-currency borrowing spread", value=False, key="ov_sf")
        if ov_sf:
            sf_pct = sb.number_input("Foreign borrowing spread (%)", value=round(s_auto * 100, 4),
                                     step=0.05, format="%.4f", key=f"in_sf_{round(s_auto, 6)}")
            s_f = sf_pct / 100
        else:
            sb.caption(f"Automatic: s* = s × (1+Rf*)/(1+Rf) = **{s_auto * 100:.4f} %**")
            s_f = None

    try:
        res = compute_wacc(beta_u, d, tau, erp, rf, rff, s, s_f)
    except ValueError as e:
        st.error(str(e))
        st.stop()

    cur = f"{country} currency"
    # ---- outputs ----
    st.subheader(f"Results for {industry}")
    table = pd.DataFrame(
        {
            "Metric": ["Relevered beta", "Cost of equity", "Pretax cost of debt",
                       "After-tax cost of debt", "WACC"],
            "USD": [f"{res['beta_e']:.4f}", pct(res["re"]), pct(res["rd"]),
                    pct(res["rd_after"]), pct(res["wacc"])],
            cur: [f"{res['beta_e']:.4f}", pct(res["re_f"]), pct(res["rd_f"]),
                  pct(res["rd_f_after"]), pct(res["wacc_f"])],
        }
    )
    st.dataframe(table, hide_index=True, width="stretch")
    st.caption(f"Currency factor F = (1+Rf*)/(1+Rf) = {res['factor']:.7f}; D/E = {res['de']:.4f}. "
               "The relevered beta is the same in both currencies.")
    raw_main = pd.DataFrame({
        "metric": ["relevered_beta", "cost_of_equity", "pretax_cost_of_debt",
                   "after_tax_cost_of_debt", "wacc"],
        "usd": [res["beta_e"], res["re"], res["rd"], res["rd_after"], res["wacc"]],
        "selected_currency": [res["beta_e"], res["re_f"], res["rd_f"], res["rd_f_after"], res["wacc_f"]],
    })
    raw_main["industry"], raw_main["country"] = industry, country
    st.download_button("Download results (CSV)", raw_main.to_csv(index=False),
                       "wacc_results.csv", "text/csv")

    st.subheader("Inputs used")
    yes = lambda b: "OVERRIDDEN" if b else "fetched"
    inputs = pd.DataFrame([
        ["Unlevered beta", f"{beta_u:.4f}", yes(ov_beta), "Damodaran betas.xls", beta_d["asof"] if beta_d else "-"],
        ["Equity risk premium", f"{erp_pct:.2f}%", "user input", "-", "-"],
        ["Debt share d", f"{d_pct}%", "user input", "-", "-"],
        [f"Tax rate ({tax_name})", f"{tax_pct:.2f}%", yes(ov_tax), "OECD" + (" (fallback table)" if using_fallback_tax else ""), tax_d["asof"]],
        ["USD BBB spread s", f"{s_pct:.2f}%", yes(ov_s), "Damodaran ratings", spr_d["asof"] if spr_d else "-"],
        ["U.S. 10y yield Rf", f"{rf_pct:.3f}%", yes(ov_rf), "Trading Economics", yld_d["asof"] if yld_d else "-"],
        [f"{country} 10y yield Rf*", f"{rff_pct:.3f}%", "= Rf (U.S.)" if is_us else yes(ov_rff),
         "Trading Economics", yld_d["asof"] if yld_d else "-"],
        ["Foreign spread s*", f"{res['s_f'] * 100:.4f}%",
         "OVERRIDDEN" if ov_sf else "automatic s × F", "calculated", "-"],
    ], columns=["Input", "Value", "Status", "Source", "As of"])
    st.dataframe(inputs, hide_index=True, width="stretch")

    # ---- all-industry comparison ----
    st.subheader("All industries under the current settings")
    if betas:
        rows = []
        for ind, b in betas.items():
            r = compute_wacc(b, d, tau, erp, rf, rff, s, s_f)
            rows.append({"Industry": ind, "Unlevered beta": b, "Relevered beta": r["beta_e"],
                         "Cost of equity USD": r["re"], "Cost of equity local": r["re_f"],
                         "WACC USD": r["wacc"], "WACC local": r["wacc_f"]})
        all_df = pd.DataFrame(rows).sort_values("WACC USD", ascending=False).reset_index(drop=True)
        st.caption("Each industry uses its own fetched unlevered beta; the beta override applies "
                   "only to the selected industry above.")
        show = all_df.copy()
        for c in show.columns[3:]:
            show[c] = (show[c] * 100).map("{:.3f}%".format)
        show["Unlevered beta"] = show["Unlevered beta"].map("{:.4f}".format)
        show["Relevered beta"] = show["Relevered beta"].map("{:.4f}".format)
        st.dataframe(show, hide_index=True, width="stretch", height=400)
        st.download_button("Download all-industry table (CSV)",
                           all_df.assign(country=country).to_csv(index=False),
                           "wacc_all_industries.csv", "text/csv")

        import plotly.graph_objects as go
        tabs = st.tabs(["Cost of equity", "WACC"])
        for tab, usd_c, loc_c, title in ((tabs[0], "Cost of equity USD", "Cost of equity local", "Cost of equity"),
                                         (tabs[1], "WACC USD", "WACC local", "WACC")):
            with tab:
                plot = all_df.sort_values(usd_c)
                fig = go.Figure()
                fig.add_bar(y=plot["Industry"], x=plot[usd_c] * 100, orientation="h", name="USD",
                            marker_color="#2a6f97")
                fig.add_bar(y=plot["Industry"], x=plot[loc_c] * 100, orientation="h", name=cur,
                            marker_color="#e07a1f")
                fig.update_layout(barmode="group", height=max(500, 18 * len(plot) * 2),
                                  xaxis_title=f"{title} (%)", margin=dict(l=10, r=10, t=30, b=10),
                                  legend=dict(orientation="h", y=1.02))
                st.plotly_chart(fig, width="stretch")
    else:
        st.info("Industry betas are unavailable, so the all-industry comparison is skipped.")

    # ---- assumptions ----
    st.subheader("Assumptions")
    st.markdown(
        "- **Risk is measured against the U.S. market**, so the equity risk premium is a U.S.-market premium; there is no separate country risk premium.\n"
        "- **U.S. industry betas proxy project risk** in any country.\n"
        "- **Government bond yields are risk-free proxies.** They may also embed liquidity, term and sovereign-default risk.\n"
        "- **Equal real rates and relative PPP** link the yield difference to expected currency depreciation, so USD figures convert to local currency with F = (1+Rf*)/(1+Rf).\n"
        "- **Constant target leverage**: the debt share d stays the same over the project's life.\n"
        "- **Interest deductions are fully usable**: the tax shield is always taken at the project tax rate.\n"
        "- **Debt beta is zero** when unlevering and relevering betas (Hamada)."
    )
    with st.expander("Formulas"):
        st.markdown(
            "D/E = d/(1−d) · βE = βU[1+(1−τ)D/E] · Re = Rf + βE·ERP · Rd = Rf + s · "
            "WACC = (1−d)Re + d·Rd(1−τ)  \n"
            "F = (1+Rf*)/(1+Rf) · Re* = (1+Re)F − 1 · s* = s·F · Rd* = Rf* + s* · "
            "WACC* = (1−d)Re* + d·Rd*(1−τ)")

    # ---- sources ----
    st.subheader("Data sources")
    srcs = pd.DataFrame([
        ["Unlevered betas by industry (Damodaran)", URL_BETAS, beta_d["asof"] if beta_d else "-", "OK" if beta_d else "failed"],
        ["Statutory corporate tax rates (OECD)", URL_OECD, tax_d["asof"], "fallback table" if using_fallback_tax else "OK"],
        ["USD Baa2/BBB spread (Damodaran)", URL_RATINGS, spr_d["asof"] if spr_d else "-", "OK" if spr_d else "failed"],
        ["10-year government yields (Trading Economics)", URL_BONDS, yld_d["asof"] if yld_d else "-", "OK" if yld_d else "failed"],
    ], columns=["Source", "URL", "As of", "Status"])
    st.dataframe(srcs, hide_index=True, width="stretch",
                 column_config={"URL": st.column_config.LinkColumn("URL")})


if __name__ == "__main__":
    main()
