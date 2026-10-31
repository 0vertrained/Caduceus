# CPAP device for obstructive sleep apnea (HCPCS E0601): source policy text

Retrieved October 31, 2026. Research notes for replacing the development paraphrase in `../cpap.yaml`
(`dev-cpap-osa@2026-09`). No YAML, code or test was changed.

## Governing documents

Mesa Verde Health is in New Mexico. New Mexico is in DME MAC Jurisdiction C (CGS), one of the four
jurisdictions that publish L33718 and A52467, so all four documents below apply to its claims.

| Document | Title and identity | Version and dates | Contractor and jurisdiction | URL read |
|---|---|---|---|---|
| NCD 240.4 | Continuous Positive Airway Pressure (CPAP) Therapy For Obstructive Sleep Apnea (OSA). Medicare NCD Manual, Pub. 100-03, section 240.4. Benefit category: Durable Medical Equipment. | Version 3. Effective 03/13/2008, implemented 08/04/2008. Other Versions table: 03/13/2008 - N/A. | CMS (national) | https://www.cms.gov/medicare-coverage-database/view/ncd.aspx?ncdid=226&ncdver=3 |
| NCD 240.4.1 | Sleep Testing for Obstructive Sleep Apnea (OSA). Pub. 100-03, section 240.4.1. | Version 1. Effective 03/03/2009, implemented 08/10/2009. No later version listed. | CMS (national) | https://www.cms.gov/medicare-coverage-database/view/ncd.aspx?ncdid=330 |
| LCD L33718 | Positive Airway Pressure (PAP) Devices for the Treatment of Obstructive Sleep Apnea | Revision R11. Original effective 10/01/2015; revision effective 01/01/2024; revision ending date N/A. Public Versions: updated 12/07/2023, effective 01/01/2024 - N/A, "Currently in Effect" (MCD `ver=52`). | DME MACs: CGS Administrators, LLC (17013, J-B; 18003, J-C) and Noridian Healthcare Solutions, LLC (16013, J-A; 19003, J-D) | https://www.cms.gov/medicare-coverage-database/view/lcd.aspx?lcdId=33718&ver=52 |
| Article A52467 | Positive Airway Pressure (PAP) Devices for the Treatment of Obstructive Sleep Apnea - Policy Article (LCD reference article) | Revision R15. Original effective 10/01/2015; revision effective 08/08/2021. Public Versions: updated 05/08/2024, effective 08/08/2021 - N/A, "Currently in Effect". | Same four DME MAC contracts | https://www.cms.gov/medicare-coverage-database/details/article-details.aspx?articleid=52467 |

Also read for the NCD 240.4 version history:
https://www.cms.gov/medicare-coverage-database/details/ncd-details.aspx?ncdid=226 and
https://www.cms.gov/medicare-coverage-database/view/ncd.aspx?ncdid=226

What I confirmed against your expectations:

- **NCD 240.4: confirmed.** It is the national CPAP policy, and version 3 is current. Every NCD page I opened shows an
  "older NCD version" banner, even version 3. The Other Versions table is the reliable signal here: it marks version 3
  "You are here" with no end date.
- **L33718: confirmed, and current.** The evaluation rule no longer says "face-to-face". The LCD's revision history records,
  under R7 (effective 01/01/2020), "Revised: "face-to-face" to "in-person" where applicable". The word "telehealth"
  appears in neither L33718 nor A52467.
- **NCD 240.4.1 is part of the chain.** L33718 revision R8 (08/08/2021) updated its sleep-test references to point to it.

How the quotes were checked: the sandbox could not download cms.gov pages, so I read them with a web reader
(WebFetch). Each quote below either came back from the reader as an exact quote, or I submitted it and the reader
compared it word for word against the live page. Every batch included deliberately altered decoy sentences, and the
reader caught all of them, down to a missing space in "≥4". Two things may differ from the rendered page:
straight versus curly quote marks and apostrophes, and the markers on nested lists (the text view shows numbers
where a browser may show letters). Sub-items are therefore cited by position.

## Verbatim sentences

### NCD 240.4, section B (Nationally Covered Indications)

- **N1** (B.1)
  > The use of CPAP is covered under Medicare when used in adult patients with OSA.
- **N2** (B.1)
  > Coverage of CPAP is initially limited to a 12-week period to identify beneficiaries diagnosed with OSA as subsequently described who benefit from CPAP.
- **N3** (B.1)
  > CPAP is subsequently covered only for those beneficiaries diagnosed with OSA who benefit from CPAP during this 12-week period.
- **N4** (B.2)
  > The provider of CPAP must conduct education of the beneficiary prior to the use of the CPAP device to ensure that the beneficiary has been educated in the proper use of the device.
- **N5** (B.2)
  > A caregiver, for example a family member, may be compensatory, if consistently available in the beneficiary's home and willing and able to safely operate the CPAP device.
- **N6** (B.3, lead-in, then its four sub-items)
  > A positive diagnosis of OSA for the coverage of CPAP must include a clinical evaluation and a positive:
  >
  > attended PSG performed in a sleep laboratory; or
  >
  > unattended HST with a Type II home sleep monitoring device; or
  >
  > unattended HST with a Type III home sleep monitoring device; or
  >
  > unattended HST with a Type IV home sleep monitoring device that measures at least 3 channels.
- **N7** (B.4)
  > The sleep test must have been previously ordered by the beneficiary’s treating physician and furnished under appropriate physician supervision.
- **N8** (B.5, lead-in, then its two sub-items)
  > An initial 12-week period of CPAP is covered in adult patients with OSA if either of the following criterion using the AHI or RDI are met:
  >
  > AHI or RDI greater than or equal to 15 events per hour, or
  >
  > AHI or RDI greater than or equal to 5 events and less than or equal to 14 events per hour with documented symptoms of excessive daytime sleepiness, impaired cognition, mood disorders or insomnia, or documented hypertension, ischemic heart disease, or history of stroke.
- **N9** (B.6; "events of per hour" is the page's own wording)
  > The AHI or RDI is calculated on the average number of events of per hour.
- **N10** (B.6)
  > If the AHI or RDI is calculated based on less than 2 hours of continuous recorded sleep, the total number of recorded events to calculate the AHI or RDI during sleep testing must be at a minimum the number of events that would have been required in a 2-hour period.
- **N11** (B.7)
  > Apnea is defined as a cessation of airflow for at least 10 seconds.
- **N12** (B.7; the same sentence also appears in section A)
  > Hypopnea is defined as an abnormal respiratory event lasting at least 10 seconds with at least a 30% reduction in thoracoabdominal movement or airflow as compared to baseline, and with at least a 4% oxygen desaturation.
- **N13** (B.8)
  > Coverage with Evidence Development (CED): Medicare provides the following limited coverage for CPAP in adult beneficiaries who do not qualify for CPAP coverage based on criteria 1-7 above.

### NCD 240.4, section C (Nationally Non-covered Indications)

- **N14**
  > Effective for claims with dates of services on and after March 13, 2008, other diagnostic tests for the diagnosis of OSA, other than those noted above for prescribing CPAP, are not sufficient for the coverage of CPAP.

### NCD 240.4.1, section B (Nationally Covered Indications)

- **S1**
  > Type I PSG is covered when used to aid the diagnosis of OSA in beneficiaries who have clinical signs and symptoms indicative of OSA if performed attended in a sleep lab facility.
- **S2**
  > Type II or Type III sleep testing devices are covered when used to aid the diagnosis of OSA in beneficiaries who have clinical signs and symptoms indicative of OSA if performed unattended in or out of a sleep lab facility or attended in a sleep lab facility.
- **S3**
  > Type IV sleep testing devices measuring three or more channels, one of which is airflow, are covered when used to aid the diagnosis of OSA in beneficiaries who have signs and symptoms indicative of OSA if performed unattended in or out of a sleep lab facility or attended in a sleep lab facility.
- **S4** (opening only; the sentence goes on to the same coverage conditions as S3)
  > Sleep testing devices measuring three or more channels that include actigraphy, oximetry, and peripheral arterial tone

NCD 240.4.1 says nothing about how recent the sleep test must be.

### LCD L33718, Coverage Indications, Limitations, and/or Medical Necessity

Initial Coverage (item 1, the E0601 paragraph):

- **L1**
  > An E0601 device is covered for the treatment of obstructive sleep apnea (OSA) if criteria A – C are met:
- **L2** (criterion A)
  > The beneficiary has an in-person clinical evaluation by the treating practitioner prior to the sleep test to assess the beneficiary for obstructive sleep apnea.
- **L3** (criterion B, lead-in)
  > The beneficiary has a sleep test (as defined below) that meets either of the following criteria (1 or 2):
- **L4** (criterion B, sub-item 1)
  > The apnea-hypopnea index (AHI) or Respiratory Disturbance Index (RDI) is greater than or equal to 15 events per hour with a minimum of 30 events; or,
- **L5** (criterion B, sub-item 2, then its two sub-items)
  > The AHI or RDI is greater than or equal to 5 and less than or equal to 14 events per hour with a minimum of 10 events and documentation of:
  >
  > Excessive daytime sleepiness, impaired cognition, mood disorders, or insomnia; or,
  >
  > Hypertension, ischemic heart disease, or history of stroke.
- **L6** (criterion C)
  > The beneficiary and/or their caregiver has received instruction from the supplier of the device in the proper use and care of the equipment.
- **L7** (unnumbered, after criteria A to C)
  > If a claim for an E0601 is submitted and all of the criteria above have not been met, it will be denied as not reasonable and necessary.

Definitions:

- **L8**
  > The apnea-hypopnea index (AHI) is defined as the average number of episodes of apnea and hypopnea per hour of sleep without the use of a positive airway pressure device.
- **L9**
  > For purposes of this policy, respiratory effort related arousals (RERAs) are not included in the calculation of the AHI.
- **L10**
  > The RDI is reported in Type III, Type IV, and other home sleep studies.
- **L11**
  > If the AHI or RDI is calculated based on less than 2 hours of sleep or recording time, the total number of recorded events used to calculate the AHI or RDI (respectively) must be at least the number of events that would have been required in a 2 hour period (i.e., must reach ≥30 events without symptoms or ≥10 events with symptoms).
- **L12**
  > Apnea is defined as the cessation of airflow for at least 10 seconds.
- **L13**
  > Hypopnea is defined as an abnormal respiratory event lasting at least 10 seconds associated with at least a 30% reduction in thoracoabdominal movement or airflow as compared to baseline, and with at least a 4% decrease in oxygen saturation.

Sleep Tests:

- **L14**
  > The sleep test must be either a polysomnogram performed in a facility-based laboratory (Type I study) or an inpatient hospital-based or home-based sleep test (HST) (Types II, III, IV, Other).
- **L15**
  > The sleep test is ordered by the beneficiary’s treating practitioner; and,
- **L16**
  > The sleep test is conducted by an entity that qualifies as a Medicare provider of sleep tests and is in compliance with all applicable state regulatory requirements.

Continued Coverage Beyond the First Three Months of Therapy:

- **L17**
  > Continued coverage of a PAP device (E0470 or E0601) beyond the first three months of therapy requires that, no sooner than the 31st day but no later than the 91st day after initiating therapy, the treating practitioner must conduct a clinical re-evaluation and document that the beneficiary is benefiting from PAP therapy.
- **L18** (lead-in, then its two items)
  > For PAP devices with initial dates of service on or after November 1, 2008, documentation of clinical benefit is demonstrated by:
  >
  > In-person clinical re-evaluation by the treating practitioner with documentation that symptoms of obstructive sleep apnea are improved; and,
  >
  > Objective evidence of adherence to use of the PAP device, reviewed by the treating practitioner.
- **L19**
  > Adherence to therapy is defined as use of PAP ≥4 hours per night on 70% of nights during a consecutive thirty (30) day period anytime during the first three (3) months of initial usage.
- **L20**
  > If the above criteria are not met, continued coverage of a PAP device and related accessories will be denied as not reasonable and necessary.
- **L21**
  > If the treating practitioner re-evaluation does not occur until after the 91st day but the evaluation demonstrates that the beneficiary is benefiting from PAP therapy as defined in criteria 1 and 2 above, continued coverage of the PAP device will commence with the date of that re-evaluation.
- **L22** (lead-in, then its two items)
  > Beneficiaries who fail the initial 12 week trial are eligible to re-qualify for a PAP device but must have both:
  >
  > In-person clinical re-evaluation by the treating practitioner to determine the etiology of the failure to respond to PAP therapy; and,
  >
  > Repeat sleep test in a facility-based setting (Type 1 study). This may be a repeat diagnostic, titration or split-night study.

Replacement:

- **L23** (replacement within the 5-year reasonable useful lifetime; fragment)
  > there is no requirement for a new clinical evaluation, sleep test, or trial period.
- **L24** (replacement after the 5-year reasonable useful lifetime)
  > There is no requirement for a new sleep test or trial period.

No sentence anywhere in L33718 sets a time limit on how recent the qualifying sleep test must be.

### Policy Article A52467

- **P1** (Non-Medical Necessity Coverage and Payment Rules)
  > No aspect of a home sleep test, including but not limited to delivery and/or pickup of the device, may be performed by a DME supplier.
- **P2** (Policy Specific Documentation Requirements, Initial Evaluation)
  > For the initial in-person evaluation, the report would commonly document pertinent information about the following elements, but may include other details.
- **P3** (Initial Evaluation list. History items, then Physical Exam items, each a separate list entry on the page)
  > Signs and symptoms of sleep disordered breathing including snoring, daytime sleepiness, observed apneas, choking or gasping during sleep, morning headaches
  >
  > Duration of symptoms
  >
  > Validated sleep hygiene inventory such as the Epworth Sleepiness Scale (see Appendices of related LCD)
  >
  > Focused cardiopulmonary and upper airway system evaluation
  >
  > Neck circumference
  >
  > Body mass index (BMI)

## Criterion map: `cpap.yaml`

| Criterion id | Real sentence(s) | Agreement |
|---|---|---|
| `osa_diagnosis` | N6 ("A positive diagnosis of OSA for the coverage of CPAP must include a clinical evaluation and a positive:" followed by the four test types), L2, L3 | **Partial.** In the real policy, the diagnosis is a clinical evaluation plus a positive qualifying sleep test. A charted diagnosis code does not establish it. Two further gaps. The coverage is for adults only (N1, N8), and the YAML has no age condition. The value set includes SNOMED 73430006 "Sleep apnea", which is broader than obstructive sleep apnea. |
| `sleep_test` | N6 and S1 to S4 (allowed test types), N7 and L15 (ordered by the treating physician or practitioner), L14, L16, P1 | **The test has a basis; "within the past year" does not.** No sentence in NCD 240.4, NCD 240.4.1 or L33718 limits how old the qualifying test may be, and L24 says replacement needs no new sleep test. The real text also requires that the treating practitioner ordered the test (N7, L15). The test must be done by an entity that qualifies as a Medicare provider of sleep tests (L16), and no part of a home test may be done by a DME supplier (P1). The YAML's code list (95810, 95806, G0399) leaves out allowed types, among them Type II and Type IV devices (for example G0398, G0400), peripheral arterial tone devices (S4) and split-night or titration studies (for example 95811). |
| `severity_qualifies` | N8, L4, L5, with L8 to L13 for how events are counted | **Partial.** Five gaps, by number below. |
| `face_to_face` | L2: "The beneficiary has an in-person clinical evaluation by the treating practitioner prior to the sleep test to assess the beneficiary for obstructive sleep apnea." | **Close.** The YAML says "a clinician"; the LCD requires the treating practitioner. The LCD has said "in-person", not "face-to-face", since R7 (01/01/2020), so the criterion's text and label are out of date. The real rule fixes the order (before the sleep test) and sets no 365-day window. NCD 240.4 by itself asks only for a "clinical evaluation" (N6); the in-person requirement and the timing come from the LCD. P2 and P3 list what the evaluation "would commonly document". That list is illustrative, not mandatory. |

The five gaps in `severity_qualifies`:

1. Impaired cognition, mood disorders and insomnia are missing from the symptom list.
2. The LCD's minimum event counts (30 events for AHI or RDI of 15 or more; 10 events for 5 to 14) and the 2-hour rule (L11) are missing.
3. The policy accepts RDI as an alternative to AHI, but the rule reads only `ahi`.
4. The rule's `any_of` has branches for daytime sleepiness, hypertension and ischemic heart disease, but none for history of stroke, although the sentence names stroke. A patient with an AHI of 5 to 14 whose only qualifying condition is a prior stroke cannot reach `met` from the chart. They get there only through the clinician attestation, whose question does mention stroke.
5. The 365-day window on the AHI observation has no basis. L8 adds that the AHI must come from a study done without PAP.

## Real requirements the YAML lacks

1. **Adults only** (N1, N8).
2. **The 12-week trial.** Initial coverage is limited to 12 weeks (N2, N3). Continued coverage needs four things:
   - a re-evaluation between day 31 and day 91 (L17);
   - an in-person re-evaluation showing the symptoms improved (L18);
   - adherence of at least 4 hours a night on 70% of nights in 30 consecutive days within the first 3 months (L19);
   - a late re-evaluation, if it happens, starts coverage from its date (L21).

   This is a continuation check rather than an initial criterion, but a prior-authorization demo should know about it.
3. **Education or instruction by the supplier** before use (N4, N5, L6).
4. **Who orders and performs the sleep test** (N7, L15, L16, P1).
5. **How events are counted:** minimum events, the 2-hour rule, AHI without PAP, RERAs excluded, and hypopnea
   scored at a 4% desaturation (L8 to L13, N9 to N12).
6. **Qualifying symptoms** of impaired cognition, mood disorders and insomnia (N8, L5).
7. **Re-qualifying after a failed trial** requires an in-person re-evaluation and a repeat facility-based Type 1 study (L22).
8. **What does not qualify.** Other diagnostic tests are not sufficient (N14). Patients outside criteria 1 to 7 are
   covered only as subjects in a qualifying study (N13).
9. **The claim rule:** an E0601 claim that does not meet all of criteria A to C is denied (L7).

L33718 also covers E0470 (a bi-level device without backup rate). It requires criteria A to C plus a failed E0601 trial,
which does not apply to E0601 requests. The LCD's CPT/HCPCS code tables are hidden behind the AMA license prompt. I did
not open or copy them.
