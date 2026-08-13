# Gaps — questions for IR

Every NOT_FOUND, CONFLICT, UNREACHABLE and SECONDARY, phrased as a question to
put to the company.

**Scope note.** Because no primary document could be opened (see `README.md` and
`fetch_log.md`), *every* metric in this dataset is UNREACHABLE. That makes the
per-company lists below near-identical by construction — they are the standing
question set, not the residue of a completed extraction. There are **no CONFLICT
entries**: recording a conflict requires two primary sources, and zero were read.

## Cross-company questions (ask all nine)

1. Do you report manufacturing capacity in **machine hours**? If so, what is the
   exact convention footnoted under the capacity table — working days per month,
   working hours per day, and any availability/uptime percentage assumed?
   *(This is the highest-value gap: hours figures are not comparable without it.
   Benchmark for the specificity wanted — Omnitech's Q1 FY27 deck reportedly
   footnotes "26 working days in a month and 22 working hours in a day",
   implying 6,864 hrs/machine/yr.)*
2. What is your **capacity-bearing machine count** (the denominator behind the
   hours figure), as of the latest reported date, and how is "capacity-bearing"
   defined — does it exclude inspection, tooling and support equipment?
3. What is **installed capacity by segment**, in the unit you report it
   (machine hours / MTPA / units per annum), with the "as of" date?
4. What was **capacity utilisation** (%) for the latest fiscal year and quarter,
   and against which capacity basis is it computed?
5. Total **manufacturing / built-up area** (sq ft or sq m), by facility?
6. Latest **capex**, its split by segment, and the **asset turn** you assume on it?
7. Any stated **forward capacity targets** and **commissioning dates** for
   facilities under construction?

## Aequs

- Research halted at the environment blocker; no company-specific documents read.
- What is installed capacity and capacity utilisation for the **aerospace
  precision-machining** segment versus the **consumer/toys** segment, separately?
- How much of the SEZ cluster's built-up area is currently occupied versus
  available for expansion?
- What is the objects-of-issue deployment schedule from the IPO, by facility and
  by year?
- *(DRHP/RHP located as existing but UNREACHABLE — see fetch_log.md.)*

## Azad Engineering

- Research halted at the environment blocker; no company-specific documents read.
- How many CNC / 5-axis machines are installed, and what is the manufacturing
  area across the Hyderabad units?
- What is installed capacity and utilisation split across **energy/turbine**
  versus **aerospace & defence** components?
- What is the capex and commissioning timeline for the new units, and the stated
  asset turn on them?

## Omnitech Engineering

- **UNREACHABLE, highest priority.** Search results indicate the company
  discloses a combined installed machining capacity in **machine hours** across
  two facilities (Metoda and Chhapara) with a stated square-metre area, as at
  end-Fiscal 2025 — and that the deck carries the working-days/hours footnote
  this dataset most needs. None of this could be read from the primary document,
  so no figure is recorded.
  - Please confirm the exact combined installed machining capacity (machine
    hours), the as-of date, and the slide it appears on in the Q1 FY27 deck.
  - Please confirm the capacity-convention footnote verbatim.
  - Please confirm the combined facility area (sq m) and the split by facility.
- What is the machine count behind the hours figure, by facility?
- What capacity utilisation did the two facilities run at in FY2025 and Q1 FY27?

## Unimech Aerospace

- **UNREACHABLE.** The RHP and DRHP are hosted on the company's own domain
  (URLs in `fetch_log.md`) but could not be fetched.
- What is installed capacity by segment, and is it expressed in machine hours?
  If so, what is the convention footnote?
- How many machines are installed across the Bengaluru facilities, and what is
  the built-up area of each?
- What was capacity utilisation in the latest fiscal year?
- What is the objects-of-issue deployment status against the original schedule,
  and has a monitoring agency reported on it?
- *(SECONDARY: a broker note dated 25 Nov 2025 was reachable and appears to
  carry segment machine-hours data. It is a broker note, barred as a figure
  source, and is not recorded. Please supply the equivalent figures from your
  own disclosure.)*

## MTAR Technologies

- How many manufacturing units are currently operational, and what is the total
  manufacturing area (sq ft)?
- How many machines are installed, and how many are capacity-bearing?
- What is the **installed annual capacity for clean-energy hotbox units**, and
  what is the scale-up roadmap?
- What was capacity utilisation in FY2024 and FY2025?
- Does any MTAR disclosure express capacity in machine hours? If not, what is
  the basis used?

## Sansera Engineering

- What is installed capacity and utilisation, split **automotive versus
  non-automotive (aero, defence, off-road)**?
- What is the total manufacturing area and machine count across the plants?
- What capex is planned, in what segment split, and at what stated asset turn?
- What is the capacity position of the **xEV / tech-agnostic** product lines
  specifically?
- What was the objects-of-issue deployment against the 2021 IPO schedule?

## Dynamatic Technologies

- What is the installed/annualised capacity of the **Aerospace** segment, and in
  what unit?
- What is the installed capacity of the **Hydraulics** segment (e.g. gear pumps
  / valves per annum)?
- What is the installed capacity of the **Automotive/Metallurgy** (foundry)
  segment, in MTPA of castings?
- What is segment-wise capacity utilisation for FY2025 and the latest quarter?
- What is total manufacturing area across Bengaluru, Coimbatore, Nashik/Chennai
  and Bristol (UK)?
- What is the commissioning date and rated capacity of the **Rear Fuselage
  Assembly Line for the D328eco** turboprop in Bangalore?
- What was FY2025/FY2026 capex and its segment split?

## Indo-MIM

- What is installed MIM capacity, in tonnes or units per annum, as of the DRHP
  date, and separately for machined and investment-cast components?
- How many injection-moulding machines and how many CNC machines are installed?
  *(SECONDARY: trade press mentions ~100 injection moulding machines — not
  recorded, as it is unverified against the DRHP.)*
- What is manufacturing area by facility? *(SECONDARY: ~1 million sq ft total,
  with Doddaballapur and Hoskote figures cited in press — not recorded.)*
- What was capacity utilisation in FY25 and FY26? *(SECONDARY: ~36.25% and
  ~32.16% cited in coverage — not recorded, and a utilisation that low needs the
  capacity basis explained.)*
- What is the objects-of-issue deployment table, the fresh-issue size, and the
  debt-repayment component?

## PTC Industries

- Research halted at the environment blocker; no company-specific documents read.
- What is installed casting capacity in **MTPA**, and separately the **titanium
  and superalloy melt capacity** at Aerolloy Technologies?
- What is the project cost, commissioning date and rated capacity of the
  Aerolloy titanium plant?
- What was capacity utilisation in FY2024 and FY2025?
- What is the deployment status of QIP proceeds, and is there a monitoring-agency
  report?
- *(CARE rationales dated Oct 2023, Jan 2025 and May 2025 and ICRA rationales
  were located but are UNREACHABLE — these typically carry the capacity and
  project-cost detail nothing else does.)*

## Method gaps to close on a re-run

- No **CONFLICT** detection was possible. Rule 2 (record both rows when two
  primary sources disagree) is untested in this dataset — with egress restored,
  expect conflicts particularly between RHP-era and current-deck capacity
  figures, and between rating-agency and company-stated capacity.
- No **DERIVED** rows exist. All arithmetic (Task 3 normalisation in particular)
  is pending a verified machine count.
