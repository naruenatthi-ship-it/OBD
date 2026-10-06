# OBDb test data (CC BY-SA 4.0)

The files in this folder come from
[OBDb/Honda-CR-V-Hybrid](https://github.com/OBDb/Honda-CR-V-Hybrid)
(commit 371b38d399ffa2a0b6d44537f354cc4cada6490e) and are shared under the
[Creative Commons Attribution-ShareAlike 4.0](https://creativecommons.org/licenses/by-sa/4.0/)
license, not the MIT license of the rest of this repository.

- `Honda-CR-V-Hybrid.json`: the signal set `signalsets/v3/default.json`, unchanged.
- `responses.json`: responses recorded from real cars and the values OBDb
  expects, taken from `tests/test_cases/2023` and `tests/test_cases/2027`
  (first three cases of the battery, odometer and DBEF commands), reformatted as JSON.

They are only used by the tests. The program itself downloads the signal set
at run time with `python -m deepal_s05 --car crv-hybrid fetch-obdb`.
