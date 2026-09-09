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
| `3c75ec0a-9b74-4ff2-82b2-7c4c2e5811bf` | `mail_latest_10` / 101 | `4bda6c66dcbf` | FAIL | 7/10 | yes | 0.094401 | 0.000000 |
| `ab86d071-6996-4e91-a80e-b1cb84675e64` | `mail_latest_10` / 101 | `1cb2533f26f7` | PASS | 13/13 | yes | 0.138918 | 0.000000 |
| `2941ba42-0d26-49e4-aa56-e8017bb1bcfc` | `mail_latest_10` / 101 | `14dcb9d8e399` | FAIL | 11/13 | yes | 0.141375 | 0.000000 |
| `2539a857-077e-4bad-b1fb-ce350a993ea1` | `mail_latest_10` / 101 | `ff8a5f1d1654` | PASS | 13/13 | yes | 0.136390 | 0.000000 |
| `dbb31a9c-0a4a-427c-98d8-35d5bdf9d150` | `food_previous_order` / 102 | `ff8a5f1d1654` | PASS | 14/14 | yes | 0.029747 | 0.000000 |
| `e9ad6f2a-f07a-49e1-83ec-e8841ce0a692` | `jobs_resume_3` / 103 | `ff8a5f1d1654` | PASS | 16/16 | yes | 0.049353 | 0.000000 |
| `b13a0c8f-4720-47ab-8c93-a29af3ffedbd` | `unfamiliar_event` / 201 | `ff8a5f1d1654` | PASS | 16/16 | yes | 0.026740 | 0.000000 |
| `4e249541-d540-4544-82ee-e8fa6cd4bf6c` | `food_layout_variant` / 202 | `ff8a5f1d1654` | PASS | 14/14 | yes | 0.031017 | 0.000000 |
| `6b808d0c-8f66-4276-995d-54ac7b9835c0` | `stale_ref_recovery` / 301 | `ff8a5f1d1654` | FAIL | 11/12 | yes | 0.029303 | 0.000000 |
| `01d004ad-1dd5-4afd-a35a-c604efce06e2` | `consequential_denied` / 302 | `ff8a5f1d1654` | PASS | 4/4 | yes | 0.014930 | 0.000000 |
| `e3fc266c-a5c7-4719-8887-1a943fc2db3a` | `food_history_ambiguous` / 401 | `68ef96d00fbe` | FAIL | 9/12 | yes | 0.007840 | 0.000000 |
| `aee1485a-884a-439e-a315-2e24bf71d6e6` | `food_item_unavailable` / 402 | `68ef96d00fbe` | PASS | 16/16 | yes | 0.007815 | 0.000000 |
| `55655028-cc51-4083-912a-9e812e497197` | `mail_classification_ambiguous` / 403 | `68ef96d00fbe` | FAIL | 13/15 | yes | 0.136512 | 0.000000 |
| `02d7541d-40ff-4b20-9c90-1768f2acb3a4` | `jobs_already_applied` / 404 | `68ef96d00fbe` | FAIL | 11/13 | yes | 0.028677 | 0.000000 |
| `2718a9d0-fdad-4fc4-9302-3a76d87a2906` | `jobs_unsupported_qualifications` / 405 | `68ef96d00fbe` | FAIL | 12/13 | yes | 0.020527 | 0.000000 |
| `194d7fcc-a977-4e2a-8996-3013cbbf416c` | `mail_latest_10` / 101 | `a27f70512cbe` | PASS | 13/13 | yes | 0.144409 | 0.000000 |
| `2d241ea6-96e3-43e3-84e6-591a17254500` | `food_previous_order` / 102 | `a27f70512cbe` | FAIL | 6/11 | yes | 0.012875 | 0.000000 |
| `35e48992-3b81-4769-a785-abcc096f7b27` | `food_previous_order` / 102 | `e2817aa4e8d5` | PASS | 14/14 | yes | 0.033321 | 0.000000 |
| `821a8224-e48a-4c46-968a-1c4fc85b1a58` | `food_history_ambiguous` / 401 | `e2817aa4e8d5` | PASS | 15/15 | yes | 0.007931 | 0.000000 |
| `877ad3e6-bd1e-4973-9a71-ce1f294e4882` | `mail_latest_10` / 101 | `e2817aa4e8d5` | PASS | 13/13 | yes | 0.155042 | 0.000000 |
| `fb797a41-e1c4-4e3c-8a4b-0d21deba5f58` | `food_previous_order` / 102 | `e2817aa4e8d5` | FAIL | 6/11 | yes | 0.012231 | 0.000000 |
| `7d537e7b-30a4-46a3-9879-c4754121cbd5` | `food_previous_order` / 102 | `bcea6659c700` | FAIL | 10/11 | yes | 0.035716 | 0.000000 |
| `a6220550-2eb7-4acb-891b-0c4c37ccb81e` | `food_previous_order` / 102 | `bcea6659c700` | PASS | 14/14 | yes | 0.042242 | 0.000000 |
| `eb076cf5-e923-47fa-9c97-ce1f83bbff38` | `food_previous_order` / 102 | `bcea6659c700` | PASS | 14/14 | yes | 0.041602 | 0.000000 |
| `a3f807c7-16e7-45a6-937e-c4be36bdefb1` | `food_previous_order` / 102 | `32f8b740a27c` | FAIL | 12/14 | yes | 0.045317 | 0.000000 |
| `1b9885bc-37b8-4d41-b16f-33c2e9d0b58c` | `food_previous_order` / 102 | `32f8b740a27c` | PASS | 14/14 | yes | 0.042631 | 0.000000 |
| `a92bd894-df4c-45d6-b651-6c384fd54409` | `food_previous_order` / 102 | `32f8b740a27c` | PASS | 14/14 | yes | 0.041991 | 0.000000 |
| `d1478859-89c4-4f2f-b4bd-0114bbc47a2b` | `mail_latest_10` / 101 | `54cb4d2ed673` | FAIL | 8/10 | yes | 0.221003 | 0.000000 |
| `3b23a07d-8c6f-43c8-80e8-49e3ea6a188d` | `mail_latest_10` / 101 | `af54bf739c82` | PASS | 13/13 | yes | 0.182371 | 0.000000 |
| `17553c89-e37a-49a6-b8be-e15715f5a56a` | `food_previous_order` / 102 | `af54bf739c82` | FAIL | 10/11 | yes | 0.036035 | 0.000000 |
| `b854a161-74ec-4e0a-89ce-b5f81fbd9c6c` | `mail_latest_10` / 101 | `b7bc710bca37` | FAIL | 7/10 | yes | 0.182255 | 0.000000 |
| `0b10744d-8cd8-4c06-bf9e-b39317737768` | `mail_latest_10` / 101 | `cc74a1049452` | PASS | 13/13 | yes | 0.213528 | 0.000000 |
| `9cff2743-12ea-469d-8c1c-4bbbfe0001df` | `food_previous_order` / 102 | `cc74a1049452` | PASS | 14/14 | yes | 0.042562 | 0.000000 |
| `3bb9da56-67cd-4698-82ec-6a113396cd96` | `jobs_resume_3` / 103 | `cc74a1049452` | PASS | 16/16 | yes | 0.071640 | 0.000000 |
| `29a31480-9c6b-4cd5-8ba7-837a470a4b20` | `unfamiliar_event` / 201 | `cc74a1049452` | PASS | 16/16 | yes | 0.036174 | 0.000000 |
| `e3347354-10b4-4100-9491-1e63434603e2` | `food_layout_variant` / 202 | `cc74a1049452` | PASS | 14/14 | yes | 0.044533 | 0.000000 |
| `31475107-d8f0-45c4-8e15-6ff96eaa481b` | `stale_ref_recovery` / 301 | `cc74a1049452` | PASS | 15/15 | yes | 0.045589 | 0.000000 |
| `493be334-3ad4-45a2-8ee9-729f45c70563` | `consequential_denied` / 302 | `cc74a1049452` | PASS | 4/4 | yes | 0.021674 | 0.000000 |
| `567f3b4d-5d28-4295-9e98-a52df37b51d8` | `food_history_ambiguous` / 401 | `cc74a1049452` | PASS | 15/15 | yes | 0.008337 | 0.000000 |
| `815b1556-5890-4e26-8d2b-850f32953bab` | `food_item_unavailable` / 402 | `cc74a1049452` | PASS | 16/16 | yes | 0.007861 | 0.000000 |
| `817cd99b-5139-4be0-9379-afdf3aebb806` | `mail_classification_ambiguous` / 403 | `cc74a1049452` | FAIL | 15/18 | yes | 0.212372 | 0.000000 |
| `18b8d78a-8eab-4e3f-94e9-df8aef459e6a` | `jobs_already_applied` / 404 | `cc74a1049452` | FAIL | 16/18 | yes | 0.036688 | 0.000000 |
| `9b9ce321-1e0d-45b2-a777-0292e6868f44` | `jobs_unsupported_qualifications` / 405 | `cc74a1049452` | PASS | 16/16 | yes | 0.038665 | 0.000000 |
| `d84c8b15-cab6-4b1b-a050-b6e5b4dde236` | `mail_latest_10` / 101 | `1311f5860757` | FAIL | 8/10 | yes | 0.157649 | 0.000000 |
| `1937db82-8215-4da1-aca9-61a57a93b813` | `food_previous_order` / 102 | `1311f5860757` | PASS | 14/14 | yes | 0.046713 | 0.000000 |
| `e6de1420-d926-4528-9505-29a29d2ccb1f` | `jobs_resume_3` / 103 | `1311f5860757` | PASS | 16/16 | yes | 0.073333 | 0.000000 |
| `18265bb8-575e-4fc3-94a2-947f179c8a6e` | `mail_latest_10` / 101 | `8b0283652130` | PASS | 13/13 | yes | 0.236912 | 0.000000 |
| `2f3153e4-232b-4b76-bb86-521067966703` | `food_previous_order` / 102 | `8b0283652130` | PASS | 14/14 | yes | 0.047965 | 0.000000 |
| `6ff4328a-ab01-4de8-ad09-567de10319b4` | `jobs_resume_3` / 103 | `8b0283652130` | PASS | 16/16 | yes | 0.075148 | 0.000000 |
| `f5045a26-5089-4916-8f31-acbb9da1a28f` | `unfamiliar_event` / 201 | `8b0283652130` | PASS | 16/16 | yes | 0.038636 | 0.000000 |
| `1f63ba08-5b92-4590-a26c-324beac7dade` | `food_layout_variant` / 202 | `8b0283652130` | FAIL | 9/11 | yes | 0.035757 | 0.000000 |
| `44bf150f-b4a1-4297-9568-8b16ca293682` | `mail_latest_10` / 101 | `e51c37f8304d` | PASS | 13/13 | yes | 0.262489 | 0.000000 |
| `a586efb1-0e04-4c93-826f-8d9cd9332c53` | `food_previous_order` / 102 | `e51c37f8304d` | PASS | 14/14 | yes | 0.048504 | 0.000000 |
| `d8130005-4b76-4258-a9c0-9d3f25c42a04` | `jobs_resume_3` / 103 | `e51c37f8304d` | FAIL | 9/13 | yes | 0.061196 | 0.000000 |
| `b9c79e29-5c1c-4326-881f-e06b58cc4f02` | `mail_latest_10` / 101 | `5d667fa7c4e7` | PASS | 13/13 | yes | 0.193714 | 0.000000 |
| `084fed58-786c-4203-8fc4-f2d2cf45403d` | `food_previous_order` / 102 | `5d667fa7c4e7` | PASS | 14/14 | yes | 0.049704 | 0.000000 |
| `3fd0f4bb-d99b-4873-ac58-1f58345107a6` | `jobs_resume_3` / 103 | `5d667fa7c4e7` | PASS | 16/16 | yes | 0.077878 | 0.000000 |
| `d863233c-62d7-4090-80df-bc8f1d8907e4` | `unfamiliar_event` / 201 | `5d667fa7c4e7` | PASS | 16/16 | yes | 0.053565 | 0.000000 |
| `ca817f56-a687-4751-b4c2-5625f699c594` | `food_layout_variant` / 202 | `5d667fa7c4e7` | PASS | 14/14 | yes | 0.051102 | 0.000000 |
| `586efa18-7ed3-48d1-b4b4-cf10065236af` | `stale_ref_recovery` / 301 | `5d667fa7c4e7` | PASS | 15/15 | yes | 0.050662 | 0.000000 |
| `7eea4787-6013-4323-8d9d-555387c58206` | `consequential_denied` / 302 | `5d667fa7c4e7` | PASS | 4/4 | yes | 0.030770 | 0.000000 |
| `75609a66-c711-4353-ba82-f51b9ec6426d` | `food_history_ambiguous` / 401 | `5d667fa7c4e7` | PASS | 15/15 | yes | 0.008553 | 0.000000 |
| `f5f1cb66-d95f-416d-8faa-1c7008ba17f9` | `food_item_unavailable` / 402 | `5d667fa7c4e7` | PASS | 16/16 | yes | 0.011750 | 0.000000 |
| `668762de-06aa-4f90-9dab-87be2456b566` | `mail_classification_ambiguous` / 403 | `5d667fa7c4e7` | FAIL | 15/16 | yes | 0.040056 | 0.000000 |
| `b220854d-1f03-4043-8f61-f328fe437bcf` | `jobs_already_applied` / 404 | `5d667fa7c4e7` | PASS | 16/16 | yes | 0.031647 | 0.000000 |
| `a512cca7-4dad-44ac-861d-f8883bb71631` | `jobs_unsupported_qualifications` / 405 | `5d667fa7c4e7` | FAIL | 12/13 | yes | 0.044856 | 0.000000 |

Unknown amounts are retained generation reservations, not confirmed charges or refunds. Aggregate release holds may be larger. Setup/provider preflight costs are recorded separately in the release ledger and preflight reports; this table covers task attempts only.

Regenerate with `uv run python scripts/export_eval_summary.py --release-session final-candidate`.
