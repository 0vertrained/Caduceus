# Policy source notes

These notes quote Medicare coverage text retrieved on October 31, 2026, and the licensing differs by source. NCD text
(NCDs 240.4, 240.4.1 and 220.2) and the quoted 42 CFR 422.101 are works of the US government. They are not subject to
copyright (17 U.S.C. 105) and may be quoted and redistributed. LCD and article text (L33718, A52467, L34220, A57206,
L36007, A56796) is written by Medicare Administrative Contractors, which are private companies, and published by CMS in
the Medicare Coverage Database. The MCD pages make no copyright claim for that prose, but it is not clearly a
government work, so a public repository should quote it in short, attributed excerpts, as these files do, rather than
mirror whole LCDs or articles. The copyright notices on LCD and article pages cover third-party code sets: the AMA's
"CPT codes, descriptions, and other data", the ADA's CDT, and the AHA's UB-04 data (revenue codes and bill types). Each
license limits use to personal or internal use and bars redistribution and derivative works, and the MCD hides those
code tables until the license is accepted. A public GitHub repository may therefore include: short verbatim policy
sentences, each with its document ID, version, section and URL; individual CPT code numbers used only as identifiers
(72148, 27447), which is common practice; and HCPCS Level II codes such as E0601 and G0399, which CMS maintains outside
the AMA license. It should not include CPT descriptors of any length (long, medium or short), CPT/HCPCS code tables
copied from the MCD, CDT content, or UB-04 tables. The YAML's `display` labels should stay plain-language wording
written in-house, not text copied from those tables. ICD-10-CM and SNOMED CT, both used in the YAML, have their own
terms of use, which were not reviewed here. This is a practical reading of the notices, not legal advice.
