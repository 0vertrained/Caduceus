# Total knee arthroplasty (CPT 27447): source policy text

Retrieved October 31, 2026. Research notes for replacing the development paraphrase in `../knee-arthroplasty.yaml`
(`dev-knee-arthroplasty@2026-09`). No YAML, code or test was changed.

## Governing documents

I found no NCD for knee replacement; coverage is set by MAC LCDs, which differ by jurisdiction. Mesa Verde Health is in
New Mexico, which is in Novitas Jurisdiction H, so **Novitas LCD L36007 governs its claims.**

| Document | Title and identity | Version and dates | Contractor and jurisdiction | URL read |
|---|---|---|---|---|
| LCD L36007 | Lower Extremity Major Joint Replacement (Hip and Knee) | Revision R10. Original effective 10/01/2015; revision effective 11/14/2019 (R10 removed the coding sections, per CMS Change Request 10901). Revision ending and retirement dates N/A. Public Versions: updated 11/08/2019, effective 11/14/2019 - N/A, "Currently in Effect". | Novitas Solutions, Inc., A and B MAC. J-H: Arkansas, Colorado, Louisiana, Mississippi, New Mexico (04211, 04212), Oklahoma, Texas. J-L: Delaware, District of Columbia, Maryland, New Jersey, Pennsylvania. | https://www.cms.gov/medicare-coverage-database/view/lcd.aspx?LCDId=36007 |
| Article A56796 | Billing and Coding: Lower Extremity Major Joint Replacement (Hip and Knee) | Original effective 08/08/2019; revision effective 01/01/2026. | Novitas, J-H and J-L | https://www.cms.gov/medicare-coverage-database/view/article.aspx?articleId=56796&ver=31 |

Other jurisdictions' knee LCDs. I checked only their identity and status, not their criteria text:

| LCD | Title | Contractor and jurisdictions | Status |
|---|---|---|---|
| L33456 | Total Joint Arthroplasty | Palmetto GBA: J-J (Alabama, Georgia, Tennessee) and J-M (North Carolina, South Carolina, Virginia, West Virginia) | Current. R20, effective 06/15/2023. Billing article A56777. https://www.cms.gov/medicare-coverage-database/view/lcd.aspx?LCDId=33456 |
| L36039 | Total Joint Arthroplasty | Wellpoint Federal (formerly National Government Services): J-6 (Illinois, Minnesota, Wisconsin) and J-K (Connecticut, Maine, Massachusetts, New Hampshire, New York, Rhode Island, Vermont) | Current. R5, effective 04/01/2026 (a corporate name change; no coverage change). Billing article A57428. https://www.cms.gov/medicare-coverage-database/view/lcd.aspx?lcdid=36039 |
| L39911 | Total Joint Arthroplasty | Wisconsin Physicians Service: J-5 (Iowa, Kansas, Missouri, Nebraska) and J-8 (Indiana, Michigan) | Original effective 10/13/2024. Its Public Versions table lists `ver=10`, effective 08/27/2026, as "Currently in Effect". I read the superseded version at https://www.cms.gov/medicare-coverage-database/view/lcd.aspx?lcdid=39911&ver=7. Billing article A59811. |
| L36577 | Total Knee Arthroplasty | Noridian, J-F | Retired. The last version shows effective dates 12/01/2019 - 11/06/2025, marked "Retired". Its billing article A57686 is also retired. https://www.cms.gov/medicare-coverage-database/view/lcd.aspx?lcdid=36577&ver=15& |

I did not find, and did not confirm the absence of, a current knee LCD from First Coast (J-N), CGS (J-15) or Noridian
J-E.

Medicare Advantage plans in New Mexico are bound by L36007, because it is a written coverage decision of the local
contractor (42 CFR 422.101(b)(3); quoted in `lumbar-mri.md`, E1). They may add internal criteria only where L36007 does
not fully establish them (422.101(b)(6)).

How the quotes were checked: the same method as `cpap.md`. Decoys were caught in every batch, among them "all of the
following" in place of "three or more" and "at least 12 weeks" in place of "usually ... 3 months or more".

## Verbatim sentences (L36007)

### Coverage Indications, Limitations, and/or Medical Necessity: Covered Indications

- **K1** (scope)
  > This LCD is only addressing medical necessity criteria for performing total hip and knee replacement surgery.
- **K2** (scope)
  > The indications outlined in this LCD are not to be applied for unicompartmental knee replacement surgery.
- **K3** (TKA lead-in to a numbered list of five criteria)
  > Medicare will consider Total Knee Arthroplasty (TKA) medically reasonable and necessary when three or more of the following criteria are met:
- **K4** (criterion 1)
  > Advanced joint disease demonstrated by radiographic supported evidence or magnetic resonance imaging (MRI), e.g., fracture or deterioration, distortion of joint surfaces, subchondral cysts, subchondral sclerosis, periarticular osteophytes, joint subluxation, joint space narrowing, avascular necrosis;
- **K5** (criterion 2)
  > History of unsuccessful appropriate conservative therapy that is clearly documented in the pre-procedure medical record.
- **K6** (criterion 2)
  > Non-surgical medical management is usually implemented for 3 months or more to assess effectiveness.
- **K7** (criterion 2)
  > Conservative treatment as clinically appropriate for the patient’s current episode of care typically includes one or more of the following: anti-inflammatory medications; analgesics; flexibility and muscle strengthening exercises with supervised physical therapy.
- **K8** (criterion 3)
  > Pain with functional disability due to arthritis or trauma to the knee joint; activities of daily living (ADLs) are diminished despite compliance with plan of care including activity restrictions as is reasonable, assistive device use, weight reduction as appropriate or therapeutic injections into the knee as appropriate;
- **K9** (criterion 4, lead-in)
  > Distinct structural abnormalities such as:

  Sub-items, paraphrased: distal femur fracture; proximal tibia fracture; malignancy (the item begins "Malignancy of
  the distal femur"); avascular or other osteonecrosis; rheumatologic changes.
- **K10** (criterion 5, lead-in)
  > Failed previous joint replacement/arthroplasty necessitating revision as indicated by any of the following:

  Sub-items, paraphrased: loosening, fracture or mechanical failure; technical or functional failure of previous knee
  surgery; previous osteotomy or partial arthroplasty; infection; periprosthetic fracture or bone loss; bearing-surface
  wear causing symptomatic synovitis.

### Limitations

- **K11** (item 1)
  > TKA and THA will not be considered reasonable and necessary when the above indications are not met.
- **K12** (item 2, lead-in)
  > Medicare will consider a total knee replacement or total hip replacement not medically reasonable and necessary when the following contraindications are present:
- **K13** (item 2, the three bullets)
  > Active infection of the hip or knee joint or active systemic bacteremia
  >
  > Active skin infection or open wound within the planned surgical site of the hip or knee
  >
  > Progressive neurological disease, etiologic for pain, instability or disability

### Provider Qualifications

- **K14**
  > Services will be considered medically reasonable and necessary only if performed by appropriately trained providers.

### General Information: Additional Documentation Requirements Specific to Joint Arthroplasty

- **K15** (lead-in to a numbered list; two fragments, quoted as the reader returned them)
  > When the procedure is indicated for advanced joint disease, in addition to the above items, ... all of the following shall be documented in the Medical Record
- **K16** (item 1)
  > Arthritis of the knee or hip supported by X-ray or MRI. The X-ray or MRI must demonstrate one of the following: subchondral cysts, subchondral sclerosis, periarticular osteophytes, joint subluxation, joint space narrowing, avascular necrosis or bone on bone articulations.
- **K17** (item 2)
  > Pain with functional disability at the hip or knee. For example, documented pain that interferes with ADLs (functional disability), or pain that is increased with initiation of activities or pain that increases with weight bearing, or pain precluding sleep.
- **K18** (item 3)
  > The documentation should demonstrate a history of a reasonable attempt (typically 3 months or more) at conservative therapy as appropriate for the patient in the current episode of care.
  >
  > For example, documented trial of NSAIDs or contraindication to such therapy and documented supervised physical therapy.
- **K19** (item 4)
  > Documentation should support that ADLs are diminished due to pain or disability despite non-surgical medical management.

Item 5, paraphrased: for patients with significant conditions or co-morbidities, the record should address the
risk and benefit of the surgery.

- **K20** (bilateral surgery; two fragments of one passage)
  > all criteria listed above would apply to the bilateral surgery when indicated.
  >
  > The medical record should also support the medical necessity for performing THA or TKA bilaterally.

L36007 never requires weight-bearing radiographs or imaging within a time window. It names no specific diagnosis
(such as osteoarthritis) and states no laterality rule beyond the bilateral passage (K20).

## Criterion map: `knee-arthroplasty.yaml`

The structure differs first. L36007 asks for **three or more of five criteria** (K3). The YAML requires all five of its
criteria (the engine's `overall` is `all_met` only when every criterion is met), and two of those five are not among
the LCD's criteria. The DSL has `any_of` and `all_of` but no "at least N of" combinator. A faithful compile needs one,
or must list the 10 three-criterion combinations inside an `any_of`.

| Criterion id | Real sentence(s) | Agreement |
|---|---|---|
| `oa_diagnosis` | K8 ("arthritis or trauma to the knee joint"), K16 ("Arthritis of the knee or hip supported by X-ray or MRI") | **No basis as a separate requirement.** The LCD never requires osteoarthritis specifically. It refers to arthritis of any kind or trauma, and criteria 4 and 5 (K9, K10) cover fractures, malignancy, osteonecrosis and failed prior arthroplasty with no arthritis at all. Requiring a primary-OA code (M17.0, M17.11, M17.12) is narrower than the policy. L36007 also has no laterality sentence beyond K20, so "the knee to be replaced" is the YAML's own rule. |
| `imaging_narrowing` | K4 (criterion 1), K16 | **Partial.** Imaging has a basis. "Weight-bearing", "within the past year" and "narrowing" as the only qualifying finding do not. X-ray or MRI both count, and any one of the listed findings qualifies: subchondral cysts, subchondral sclerosis, periarticular osteophytes, joint subluxation, joint space narrowing, avascular necrosis or bone-on-bone articulation (with fracture or deterioration and distortion of joint surfaces in K4). |
| `conservative_12_weeks` | K5, K6 ("usually implemented for 3 months or more"), K7, K18 | **Partial.** Unsuccessful conservative therapy is criterion 2, but the duration is guidance ("usually", "typically"), not a hard minimum of 12 weeks. The LCD's conservative-therapy list is anti-inflammatory medications, analgesics, and flexibility and strengthening exercise with supervised physical therapy (K7). The documentation example is an NSAID trial (or a contraindication to one) plus supervised physical therapy (K18). A knee injection alone, which satisfies the YAML's `any_of`, is not in criterion 2. Injections appear only in criterion 3's plan of care (K8). |
| `functional_limitation` | K8 (criterion 3), K17, K19 | **Basis exists; wording is looser.** The LCD requires pain *with* functional disability, and ADLs diminished *despite* compliance with a plan of care or non-surgical management. The YAML accepts "pain or loss of function" with no link to failed treatment. |
| `no_active_infection` | K12, K13 ("Active infection of the hip or knee joint or active systemic bacteremia") | **Basis exists; incomplete.** The contraindication also covers active systemic bacteremia, an active skin infection or open wound at the planned surgical site, and progressive neurological disease that causes the pain, instability or disability. Treating absence as `met` (`when_absent: met`) fits the LCD's framing: contraindications, when present, make the surgery not reasonable and necessary. |

## Real requirements the YAML lacks

1. **The three-of-five rule** (K3), as described above.
2. **Criteria 4 and 5:** distinct structural abnormalities, and failed prior arthroplasty needing revision (K9, K10).
3. **Contraindications beyond joint infection:** systemic bacteremia, skin infection or open wound at the surgical site,
   and progressive neurological disease (K13).
4. **Documentation for advanced joint disease** (K15 to K19). Item 5 also calls for a risk and benefit discussion when
   the patient has significant co-morbidities.
5. **Provider qualifications** (K14).
6. **Bilateral surgery:** all criteria apply, and the record must support doing both sides (K20).
7. **Scope:** the LCD does not apply to unicompartmental (partial) knee replacement (K2).

I could not see A56796's ICD-10-CM group for knee procedures. The page text available to the reader ended inside the
hip group's fracture codes, so I could not confirm that M17.0, M17.11 and M17.12 are listed. Check the list in the MCD.
The CPT code tables are behind the AMA license prompt. I did not open or copy them.
