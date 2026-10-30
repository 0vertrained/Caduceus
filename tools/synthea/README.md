# Synthea, built from source

`make data` runs [Synthea](https://github.com/synthetichealth/synthea) to generate the synthetic patients. Upstream's
published jar bundles library versions with known vulnerabilities (106 findings from Grype on 2026-10-30: HAPI FHIR
6.1.0 on HL7 FHIR core 5.6.36, Jackson 2.13, SnakeYAML 1.33, XStream 1.4.18, commons-text 1.9 and others). So the
jar is built here instead:

- `build.sh` fetches Synthea at the commit upstream's jar was built from, checks the Gradle wrapper's checksum,
  applies `synthea.patch`, and builds the same `uberJar` upstream publishes.
- `synthea.patch` changes library versions and nothing else in the simulation: HAPI FHIR 8.12.1 (HL7 FHIR core
  6.9.12), Jackson 2.22.4 for every module, Guava 33.7.2, SnakeYAML 2.5, commons-text 1.15.0, Log4j 2.26.1, and
  constraints that lift transitive libraries to fixed releases. Two source changes follow the libraries' own API
  changes: SnakeYAML 2 needs explicit `LoaderOptions` (three lines), and HL7 FHIR core 6 moved its FHIRPath
  classes to `org.hl7.fhir.r4.fhirpath` (imports in the flexporter, which `make data` does not use).

Two checks keep this honest: CI fails if Grype finds any vulnerability with a released fix in the built jar, and
the dataset must regenerate byte for byte, which shows the upgrades did not change what Synthea generates. The
build needs the network; running Synthea afterwards does not have it (`scripts/offline.sh`).
