# Synthetic evaluation attempts

Generated from retained local case reports. Every attempt is included; an overall failure remains a failure even when browser-state checks succeed. Different runtime fingerprints are different implementation versions, so these attempts are not a controlled reliability estimate. Real-account observations and credentials are excluded.

See [VALIDATION.md](VALIDATION.md) for release status and [FINAL-TEST.md](FINAL-TEST.md) for required gates. Trace verification means the actual root and nested events were retrieved from LangSmith; it does not mean the task passed. Full traces remain in the configured private LangSmith workspace.

| Run ID | Case / seed | Runtime | Overall | Checks passed | Trace verified | Settled USD | Unknown USD |
| --- | --- | --- | --- | --- | --- | ---: | ---: |
| `94b9d0de-ce8d-4e6e-bf55-cdf6661a41f9` | `mail_latest_10` / 101 | `8fb9c7441641` | FAIL | 4/7 | yes | 0.023272 | 0.003363 |
| `4b6f0362-61f4-4d4f-abfa-17425baedab1` | `mail_latest_10` / 101 | `c4588ddc86a5` | FAIL | 4/7 | yes | 0.025538 | 0.000000 |
| `735c7a8f-532d-44ec-8911-1d0dabddef6b` | `mail_latest_10` / 101 | `621b38e451cc` | FAIL | 5/7 | yes | 0.093107 | 0.000000 |
| `9bb2712e-e080-4dd0-b9fd-6b08087821dd` | `mail_latest_10` / 101 | `93ceaa52d6d9` | FAIL | 6/10 | yes | 0.008081 | 0.000000 |
| `a8700a55-dd44-47fd-9077-b593d0730328` | `mail_latest_10` / 101 | `b4b740b204c1` | FAIL | 9/10 | yes | 0.087777 | 0.000000 |
| `3e1e9df4-4aa6-471d-bcad-7b5886eb813f` | `mail_latest_10` / 101 | `4bd7627a55c9` | FAIL | 8/10 | yes | 0.179216 | 0.000000 |
| `f6f19cb7-2794-442b-9853-6a540ad41958` | `mail_latest_10` / 101 | `df50c24b97b5` | PASS | 13/13 | yes | 0.133253 | 0.000000 |
| `1c569c02-6d93-4009-9c3c-564e09ca6f64` | `food_previous_order` / 102 | `df50c24b97b5` | FAIL | 8/11 | yes | 0.017098 | 0.000000 |
| `5ef709e5-0271-4736-84a3-381f6f702d0f` | `mail_latest_10` / 101 | `a4ab2a3f9773` | FAIL | 9/10 | yes | 0.106078 | 0.000000 |
| `5a4bed86-b2d2-45e0-bd24-5e42e0dc7ba1` | `food_previous_order` / 102 | `a4ab2a3f9773` | FAIL | 9/11 | yes | 0.024323 | 0.000000 |
| `f8866318-5172-4544-b0cf-793df3ceae02` | `jobs_resume_3` / 103 | `a4ab2a3f9773` | FAIL | 6/13 | yes | 0.010428 | 0.000000 |
| `5245aa63-7313-4cd1-820d-b78c17ff59bb` | `mail_latest_10` / 101 | `1aa4d712aa55` | PASS | 13/13 | yes | 0.103433 | 0.000000 |
| `ed2c840e-7b14-4b59-b847-c4147c40adca` | `food_previous_order` / 102 | `1aa4d712aa55` | FAIL | 10/11 | yes | 0.029961 | 0.000000 |
| `d2f82fa4-f961-4c50-8750-0203a28949c1` | `jobs_resume_3` / 103 | `1aa4d712aa55` | FAIL | 11/13 | yes | 0.044211 | 0.000000 |
| `8ee172f2-b776-4cd3-af0b-e3add6bc7191` | `mail_latest_10` / 101 | `924a3ab5d5bc` | PASS | 13/13 | yes | 0.102362 | 0.000000 |
| `fbe24d0d-2de9-4da4-8f87-4ef9f6c557ba` | `food_previous_order` / 102 | `924a3ab5d5bc` | PASS | 14/14 | yes | 0.031554 | 0.000000 |
| `87f762d0-df45-4981-8bf9-dde0f699ba25` | `jobs_resume_3` / 103 | `924a3ab5d5bc` | PASS | 16/16 | yes | 0.047723 | 0.000000 |
| `766af245-efd2-43ec-85d9-98b77d3cf063` | `unfamiliar_event` / 201 | `924a3ab5d5bc` | FAIL | 11/13 | yes | 0.029510 | 0.000000 |
| `3441d126-f1c7-47f7-bcf3-a838196ceff9` | `food_layout_variant` / 202 | `924a3ab5d5bc` | PASS | 14/14 | yes | 0.029061 | 0.000000 |

Unknown amounts are retained generation reservations, not confirmed charges or refunds. Aggregate release holds may be larger. Setup/provider preflight costs are recorded separately in the release ledger and preflight reports; this table covers task attempts only.

Regenerate with `uv run python scripts/export_eval_summary.py --release-session final-candidate`.
