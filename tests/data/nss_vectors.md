# NSS test vectors

The Spanish social security number (NSS, "número de la Seguridad Social") has 12 digits:
`PP NNNNNNNN CC`

- `PP`: province code (2 digits).
- `NNNNNNNN`: number within the province (8 digits, zero-padded).
- `CC`: control digits (2 digits).

Algorithm (as published by secondary sources):

- If `N < 10_000_000` (10^7): `CC = (N + PP * 10^7) mod 97`.
- Otherwise: `CC = (PP * 10^8 + N) mod 97`, i.e. the concatenation `PPNNNNNNNN` mod 97.

No official TGSS source publishes the algorithm; vectors derived from the published algorithm.
python-stdnum has no NSS module, so these vectors (ADR-0005) are the reference for the tests.
All values are synthetic.

| NSS | Expected | Arithmetic and source |
|---|---|---|
| 28 12345678 40 | valid | Published example on intervia.com (secondary source). N = 12345678 >= 10^7, so 2812345678 mod 97 = 40. |
| 28/12345678/40 | valid | Same number written with slashes. |
| 281234567840 | valid | Same number without separators. |
| 28 12345678 41 | invalid | Same number, control off by one. |
| 28 01234567 42 | valid | N = 1234567 < 10^7, so (1234567 + 28 * 10^7) mod 97 = 281234567 mod 97 = 42. |
| 28 01234567 85 | invalid | 85 = 2801234567 mod 97, the concatenation result, which is wrong for the N < 10^7 branch. |
| 29 12345678 40 | invalid | Province changed: the control no longer matches. |
