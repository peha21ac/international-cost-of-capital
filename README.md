# Cost of equity and WACC: USD vs. a selected currency

A single-file Streamlit app (`app.py`) that fetches current market data and compares a
project's cost of equity and WACC in U.S. dollars and in the currency of a chosen country.

## Setup

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
python test_calc.py          # 3 assert-style tests of the calculation (or: pytest)
```

**Streamlit Community Cloud:** push `app.py`, `requirements.txt` and `README.md` to a GitHub
repo, create the app pointing at `app.py`, and set Python 3.11 under *Advanced settings*.
No secrets or API keys are needed.

## Data sources (fetched live, cached 1-6 hours, "Refresh source data" clears the cache)

| Item | Source | Notes |
|---|---|---|
| Unlevered beta by U.S. industry | [Damodaran betas.xls](https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls) | Header row is located by searching for "Industry Name"; the plain "Unlevered beta" column is used, never the cash-corrected one. Data date parsed from the file. |
| Corporate tax rates | [OECD statutory CIT rates](https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/full-report/statutory-corporate-income-tax-rates_440f9286.html) | Largest table with country rows and year columns; latest year with data. If scraping fails, an embedded **approximate** table is used with a visible warning. |
| USD BBB spread | [Damodaran ratings](https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html) | Baa2/BBB row of the large non-financial firms table, in % over the U.S. 10-year yield. |
| 10-year yields | [Trading Economics](https://tradingeconomics.com/bonds) | Scraped with a browser User-Agent. Country-name mismatches handled (e.g. Korea / South Korea, Türkiye / Turkey, Czechia / Czech Republic). The page has no date, so "as of" is the retrieval time. |

Every fetch fails gracefully: a warning is shown, and the affected input switches to manual entry.
Web scraping is fragile; if a site changes its layout, use the override boxes and check the
source warnings.

## Formulas (decimals internally, rounded only for display)

- D/E = d / (1 − d)
- βE = βU × [1 + (1 − τ) × D/E]
- Re = Rf + βE × ERP
- Rd = Rf + s, after tax Rd × (1 − τ)
- WACC = (1 − d) × Re + d × Rd × (1 − τ)
- F = (1 + Rf\*) / (1 + Rf)
- Re\* = (1 + Re) × F − 1
- s\* = s × F (unless overridden)
- Rd\* = Rf\* + s\*, after tax Rd\* × (1 − τ)
- WACC\* = (1 − d) × Re\* + d × Rd\* × (1 − τ)

Rf = U.S. 10-year yield, Rf\* = foreign 10-year yield, τ = project tax rate (same in both
currencies), d = debt share, s = USD spread. If the selected country is the U.S., F = 1 and both
columns are identical. d = 0 gives βE = βU and WACC = Re; d = 100% is rejected.

## Assumptions

Risk is measured against the U.S. market; U.S. industry betas proxy project risk; government
yields are risk-free proxies (they may include liquidity, term and sovereign risk); equal real
rates and relative PPP link yield differences to expected depreciation; no separate country risk
premium; constant target leverage; interest deductions fully usable; zero debt beta.

## Code layout

`compute_wacc(...)` is a pure function (no Streamlit calls) and is covered by `test_calc.py`:
the Colombia example, equal yields give identical results, and d = 0 gives WACC = Re.
