# MRI of the lumbar spine without contrast (CPT 72148): source policy text

Retrieved October 31, 2026. Research notes for replacing the development paraphrase in `../lumbar-mri.yaml`
(`dev-lumbar-mri@2026-09`). No YAML, code or test was changed.

## Governing documents

There is no lumbar-specific NCD. The national MRI NCD (220.2) applies everywhere. One MAC has a lumbar MRI LCD:
Noridian's L34220, which covers Jurisdictions E and F only.

| Document | Title and identity | Version and dates | Contractor and jurisdiction | URL read |
|---|---|---|---|---|
| NCD 220.2 | Magnetic Resonance Imaging. Pub. 100-03, section 220.2. | Version 6. Effective 04/10/2018, implemented 12/10/2018. The Other Versions table marks it "You are here"; the page shows the same "older version" banner as every NCD page. | CMS (national) | https://www.cms.gov/medicare-coverage-database/view/ncd.aspx?NCDId=177 |
| LCD L34220 | Lumbar MRI | Revision R10. Original effective 10/01/2015; revision effective 10/23/2025; revision ending and retirement dates N/A. Public Versions: updated 10/14/2025, effective 10/23/2025 - N/A, "Currently in Effect". The prior version (`ver=37`) ran 08/18/2022 - 10/22/2025. | Noridian Healthcare Solutions, LLC, A and B MAC. J-E: California, Nevada, Hawaii, Guam, American Samoa, Northern Mariana Islands. J-F: Alaska, Idaho, Oregon, Washington, Arizona, Montana, North Dakota, South Dakota, Utah, Wyoming. | https://www.cms.gov/medicare-coverage-database/view/lcd.aspx?LCDId=34220 |
| Article A57206 | Billing and Coding: Lumbar MRI | Original effective 10/01/2019; revision effective 10/23/2025 (R8 per Noridian's notice). Its ICD-10-CM group of codes that support medical necessity lists 1,242 codes. | Noridian, J-E and J-F | https://www.cms.gov/medicare-coverage-database/view/article.aspx?articleId=57206&ver=30 |

Supporting contractor pages I read:

- Noridian JF notice. Effective 10/23/2025 it retired the JF LCD L37281 "Lumbar MRI" and article A57207, and
  replaced them with L34220 and A57206 "to consolidate JF policies with JE policies":
  https://med.noridianmedicare.com/web/jfb/article-detail/-/view/10534/multiple-lcds-and-billing-and-coding-articles-retirement-effective-october-23-2025
- Noridian JE notice listing "Lumbar MRI - R10" and "Billing and Coding: Lumbar MRI - R8", effective 10/23/2025:
  https://med.noridianmedicare.com/web/jeb/article-detail/-/view/10525/policy-revision-s-for-local-coverage-determinations-and-associated-billing-and-coding-articles-effective-october-23-2025
- Noridian JE medical review page for 72148, which names L34220, A57206 and NCD 220.2 as the governing documents:
  https://med.noridianmedicare.com/web/jeb/cert-review/mr/review-results/72148

### Which document governs a New Mexico claim

Mesa Verde Health is in New Mexico. New Mexico's A/B MAC is Novitas Solutions, Jurisdiction H: the contractor table of
Novitas LCD L36007 lists New Mexico contracts 04211 and 04212 under J-H. **L34220 does not govern New Mexico claims.**
I found no lumbar or spine MRI LCD from Novitas, or from any MAC other than Noridian.

That negative comes from web searches and contractor pages. The MCD's own search and reports were not reachable from
this session, so it is not an exhaustive MCD query. Check it once in the MCD with keyword "72148" and state
"New Mexico".

For traditional Medicare in New Mexico, then, the only public Medicare text is NCD 220.2. Its covered-use list is, in
its own words (M2), "general guidelines or examples" rather than criteria.

Medicare Advantage plans are bound by NCDs and by the LCDs of the MAC with jurisdiction in their area
(42 CFR 422.101(b)(1) and (b)(3), E1 below). Where criteria are not fully established, including where "There is an
absence of any applicable Medicare statutes, regulations, NCDs or LCDs setting forth coverage criteria" (E3), a plan
may apply its own internal coverage criteria. Those criteria must be publicly accessible and based on widely used
guidelines or clinical literature (E2). The plan must also publish the criteria, a summary of the evidence, the
sources and its rationale (422.101(b)(6)(ii)).

In practice, Medicare Advantage plans send advanced-imaging requests to radiology benefit managers. One example:
Carelon's Medicare Advantage page says it applies its own publicly accessible guidelines, or a plan's policy, where
Medicare criteria are not fully established
(https://guidelines.carelonmedicalbenefitsmanagement.com/for-medicare-beneficiaries-enrolled-in-a-medicare-advantage-plan/).
Those benefit-manager guidelines are proprietary and were not used.

A plan could adopt criteria like L34220's as its own published internal criteria. That is my reading of (b)(6), not
something a source states, and L34220 itself binds only J-E and J-F claims. The demo could use L34220 as an openly
labeled out-of-jurisdiction model, or set the case in J-E or J-F.

How the quotes were checked: the same method as `cpap.md`. Each sentence was returned by the web reader or confirmed
word for word against the live page, with decoys caught in every batch. In this LCD the reader reported straight
quotation marks around "red flag" in some sentences and curly ones in another. The wording is verified; the
quote-mark style may not match the page.

## Verbatim sentences

### NCD 220.2, section B.1 (MRI)

- **M1** (B.1, introductory paragraph)
  > Although several uses of MRI are still considered investigational and some uses are clearly contraindicated (see subsection C), MRI is considered medically efficacious for a number of uses.
- **M2** (B.1, introductory paragraph)
  > Use the following descriptions as general guidelines or examples of what may be considered covered rather than as a restrictive list of specific covered indications.
- **M3** (B.1, introductory paragraph)
  > In addition, the services must be reasonable and necessary for the diagnosis or treatment of the specific patient involved.
- **M4** (B.1, item 1)
  > MRI is useful in examining the head, central nervous system, and spine.
- **M5** (B.1, item 3)
  > MRI may also be covered to diagnose disc disease without regard to whether radiological imaging has been tried first to diagnose the problem.

The rest of NCD 220.2 is paraphrased here, not quoted. Section B.3 sets conditions for MRI in patients with implanted
pacemakers, ICDs, CRT-P or CRT-D devices (use according to FDA labeling). Section C lists:

- C.1 contraindications, among them a viable pregnancy;
- C.2 nationally non-covered uses, among them MRI of cortical bone and calcifications, and MRI for patients with
  metallic clips on vascular aneurysms.

### LCD L34220, Coverage Indications, Limitations, and/or Medical Necessity

- **R1** (the page has a space before the comma)
  > Lumbar MRI may be indicated for a patient with a "red-flag" condition , such as a suspected tumor, infection, herniated intervertebral disc with nerve compression, or a major neurological problem.
- **R2**
  > The MRI test result may be needed to evaluate these conditions to determine the need for surgery or other aggressive therapy, such as a work-up for metastatic cancer.
- **R3** (opening of the sentence; it goes on to list examination elements)
  > "Red flags" are identified through an appropriate history plus a physical examination
- **R4** (the red-flag list. A single flat bulleted list of 17 items introduced by "Red flags" include:, with no category headings)
  > Major trauma; Minor trauma in a potentially osteoporotic patient; History of cancer; Fever; Chills; Unexplained weight loss; Recent bacterial infection; IV drug abuse; Immune suppression; Pain that worsens when supine or at night; Saddle anesthesia; Recent onset of bladder dysfunction; Clinically significant or progressive neurologic deficit in the lower extremity; Unexpected laxity of the anal sphincter; Perianal or perineal sensory loss, / Clinically significant motor weakness, or / Other nerve root compromise

  On the page each item is its own bullet. The semicolons and slashes here only separate them.
- **R5**
  > Eighty (80) to ninety (90) percent of patients with low back pain improve one month after symptom onset even without treatment.
- **R6**
  > Therefore, spinal imaging tests are not generally necessary during the first month of symptoms except when a "red flag" (suggesting a medically emergent condition) is noted on the medical history and physical examination.
- **R7**
  > For a "non-red flag" condition, the MRI may be appropriate after 1 month of symptoms.
- **R8** ("four weeks" is bold on the page)
  > For example, for a patient with low back pain syndrome where there is no known injury, history of cancer, or septic disorder and there are no symptoms or signs suggesting nerve root disorder or spinal cord dysfunction (i.e. no "red flags"), MRI will be covered only if the patient has not responded to a reasonable trial of conservative management lasting at least four weeks.
- **R9**
  > If a patient's limitations due to low back symptoms do not improve within four weeks, findings on reassessment may reveal an indication for a MRI.
- **R10**
  > However, since MRI changes are common in asymptomatic patients, MRI abnormalities alone do not retrospectively validate the need for the test without other supporting clinical rationale.
- **R11**
  > A lumbar MRI used to evaluate uncomplicated degenerative disc disease or herniated nucleus pulposus is not considered medically necessary when a surgical intervention or other aggressive treatment (e.g. intervertebral joint injection) is not under consideration.
- **R12**
  > When a lumbar MRI is ordered, Medicare expects that the information gained from the test will be used for medical decision-making.

The LCD contains neither "six weeks" nor "6 weeks". It names no specific forms of conservative management, such as
physical therapy or medication. It sets no requirement for plain radiographs.

### LCD L34220, Documentation Requirements

- **R13**
  > The clinical findings and relevant prior treatment that support the need for the MRI must be documented in the MRI report or clinical record and made available to the contractor upon request.

### Article A57206, coding guidance

- **B1**
  > An attending/treating physician's order is required for each test.
- **B2**
  > Normally only one lumbar MRI is sufficient to diagnose the patient's condition.
- **B3**
  > The documentation must clearly support the ICD-10-CM code(s) reported on the claim.

I could not confirm whether the YAML's diagnosis codes (M54.50, M54.59, M54.16, M54.17) are in A57206's 1,242-code list.
The page text available to the reader stopped at M05.052, before the M54 range. Check the list in the MCD.

### 42 CFR 422.101 (Medicare Advantage)

Read at https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-422/subpart-C ("up to date as of 9/29/2026"),
and the mirror at https://www.law.cornell.edu/cfr/text/42/422.101

- **E1** ((b)(3); (b)(1) is "CMS's national coverage determinations")
  > Written coverage decisions of local Medicare contractors with jurisdiction for claims in the geographic area in which services are covered under the MA plan.
- **E2** ((b)(6))
  > MA organizations may create publicly accessible internal coverage criteria that are based on current evidence in widely used treatment guidelines or clinical literature when coverage criteria are not fully established in applicable Medicare statutes, regulations, NCDs or LCDs.
- **E3** ((b)(6)(i)(C))
  > There is an absence of any applicable Medicare statutes, regulations, NCDs or LCDs setting forth coverage criteria.

## Criterion map: `lumbar-mri.yaml`

| Criterion id | Real sentence(s) | Agreement |
|---|---|---|
| `lbp_duration` | R6, R7 ("the MRI may be appropriate after 1 month of symptoms"), R5 | **Disagrees on the number.** The LCD's waiting period is one month of symptoms, not six weeks (42 days). It applies only to patients without red flags; a red-flag patient has no waiting period (R6). NCD 220.2 sets no duration. |
| `conservative_care` | R8: "MRI will be covered only if the patient has not responded to a reasonable trial of conservative management lasting at least four weeks." R9 | **Basis exists; the threshold and scope differ.** The LCD requires at least four weeks, not six. It names no type of treatment, so the YAML's physical-therapy-only codes are narrower than the policy. The real test is failure to respond, not just that care came first. It applies only when there are no red flags. R8 is phrased as an example ("For example, for a patient with ..."), but it is the only conservative-care rule the LCD states. |
| `recent_xray` | None in L34220. NCD 220.2 M5: "MRI may also be covered to diagnose disc disease without regard to whether radiological imaging has been tried first to diagnose the problem." | **No basis in the real policy, and the NCD contradicts it.** L34220 has no radiograph requirement, and the national NCD says prior radiological imaging is not a precondition for disc disease. |
| `red_flags_excluded` | R1, R2, R3, R4, R6 | **Inverted.** In the LCD, a red flag is a route to coverage. A red-flag condition may justify MRI right away (R1, R6). The absence of red flags is only the condition under which the one-month and four-week rules apply (R8). Requiring red flags to be absent before approval would block the urgent cases the LCD covers first. The LCD's list (R4) also has 17 specific findings; the YAML names four categories. |

How the real policy fits together, in rule form: **(red flag present)** OR **(no red flag AND at least 1 month of
symptoms AND no response to at least 4 weeks of conservative management)**. On either path the test must be
reasonable and necessary for this patient (M3), and its results must be used for decisions (R12). The current DSL can
express this with `any_of` and `all_of` inside one criterion. The existing four separate required criteria cannot.

## Real requirements the YAML lacks

1. **The red-flag pathway** (R1, R2, R4, R6), described above.
2. **Results must be used** for medical decision-making (R12).
3. **No MRI for uncomplicated degenerative disc disease or herniated nucleus pulposus** when no surgery or other
   aggressive treatment is under consideration (R11).
4. **Abnormal findings do not justify the test after the fact** (R10).
5. **Documentation of the clinical findings and relevant prior treatment** in the MRI report or clinical record (R13).
6. **An attending or treating physician's order for each test**, and normally only one lumbar MRI (B1, B2).
7. **NCD 220.2's contraindications, non-covered uses and implanted-device conditions** (sections B.3 and C,
   paraphrased above).

The CPT/HCPCS code tables in L34220 and A57206 are behind the AMA license prompt. I did not open or copy them.
