# Held-out labels to check by hand

Each item gives the policy, what the structured record holds, and what the packet says. Tick it if
the expected answer is what a careful prior-authorization reviewer would conclude from the structured
record under the policy. Text found only in the packet does not make a criterion met: that is what
the clinician's attestation is for. Note anything you disagree with; a wrong label is a finding.

- [ ] **HH-003 face_to_face** (CPAP device for obstructive sleep apnea): expected **unknown**
  - Record: Observation sleep-evaluation Face-to-face sleep evaluation: 2025-09-02, value Completed, status final
  - Packet, page 8: "S1eep medicine consult in person Sep 2: snoring, witnessed apneas, morning headaches."
- [ ] **HH-004 red_flags_excluded** (MRI of the lumbar spine without contrast): expected **unmet**
  - Record: Observation red-flag-screen Red-flag symptom screen: 2026-08-11, value Negative, status final; Observation red-flag-screen Red-flag symptom screen: 2026-10-01, value Positive, status final
  - Packet, page 5: "Positive red-flag screen: unintentional 15 lb weight loss and night sweats."
- [ ] **HH-007 conservative_care** (MRI of the lumbar spine without contrast): expected **unknown**
  - Record: nothing charted for this criterion
  - Packet, page 5: "Reports a prior course of PT elsewhere, about 9 wks, records not available."
- [ ] **HH-008 no_active_infection** (Total knee arthroplasty, left knee): expected **met**
  - Record: Condition M00.9 Pyogenic arthritis, unspecified: onset 2025-06-11, clinical status resolved, resolved 2025-08-10
  - Packet, page 10: "Remote joint infection (2025), completed antibiotics, no recurrence."
- [ ] **HH-013 recent_xray** (MRI of the lumbar spine without contrast): expected **met**
  - Record: Procedure 72110 X-ray lumbosacral spine, 4 or more views: 2026-08-14, status completed
  - Packet, page 10: "Plain films of the lumbosacral spine dated 08/14/2026: mild disc height loss at L5-S1."
- [ ] **HH-014 oa_diagnosis** (Total knee arthroplasty, right knee): expected **met**
  - Record: Condition M17.11 Unilateral primary osteoarthritis, right knee: onset 2023-06-12, clinical status active
  - Packet, page 9: "Impression: DJD right knee, tricompartmental."
- [ ] **HH-020 functional_limitation** (Total knee arthroplasty, left knee): expected **unmet**
  - Record: Observation functional-limitation Functional limitation assessment: 2026-08-03, value Absent, status final
  - Packet, page 3: "Independent with ADLs, walks two miles daily without difficulty."
- [ ] **HH-022 red_flags_excluded** (MRI of the lumbar spine without contrast): expected **met**
  - Record: Observation red-flag-screen Red-flag symptom screen: 2026-10-13, value Positive, status entered-in-error; Observation red-flag-screen Red-flag symptom screen: 2026-09-12, value Negative, status final
  - Packet, page 10: "Screened for red-f1ag symptoms; none present."
- [ ] **HH-028 red_flags_excluded** (MRI of the lumbar spine without contrast): expected **met**
  - Record: Observation red-flag-screen Red-flag symptom screen: 2026-09-21, value Negative, status final
  - Packet, page 9: "Cauda equina screen: denies saddle nurnbness, no bladder or bowel dysfunction, afebri1e, no hx of malignancy."
- [ ] **HH-030 sleep_test** (CPAP device for obstructive sleep apnea): expected **met**
  - Record: Procedure 95810 Polysomnography, attended: 2025-10-28, status completed
- [ ] **HH-032 imaging_narrowing** (Total knee arthroplasty, right knee): expected **met**
  - Record: Procedure 73562 X-ray knee, 3 views: 2026-07-30, status completed; Observation knee-joint-space Knee joint space on radiograph: 2026-07-30, value Narrowed, status final
  - Packet, page 8: "Standing AP/lat Right knee (Jul 30): severe medial compartment narrowing, subchondral sclerosis."
- [ ] **HH-033 face_to_face** (CPAP device for obstructive sleep apnea): expected **unknown**
  - Record: Observation sleep-evaluation Face-to-face sleep evaluation: 2025-09-25, value Completed, status final
  - Packet, page 8: "Sleep medicine consult in person 2025-09-25: snoring, witnessed apneas, morning headaches."
- [ ] **HH-034 conservative_care** (MRI of the lumbar spine without contrast): expected **unmet**
  - Record: Procedure 97110 Therapeutic exercise: 2025-03-26 to 2025-05-25, status completed
  - Packet, page 6: "Rehab summary: physiotherapy 03/26/2025 through May 25, plateaued, referred back to ortho."
- [ ] **HH-035 oa_diagnosis** (Total knee arthroplasty, left knee): expected **unmet**
  - Record: Condition M17.11 Unilateral primary osteoarthritis, right knee: onset 2025-11-25, clinical status active
  - Packet, page 6: "OA confined to the right knee per prior films; left knee exam benign."
- [ ] **HH-036 osa_diagnosis** (CPAP device for obstructive sleep apnea): expected **met**
  - Record: Condition 78275009 Obstructive sleep apnea syndrome: onset 2026-09-10, clinical status active
  - Packet, page 5: "Assessrnent: OSA, new diagnosis."
