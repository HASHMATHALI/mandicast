# MandiCast documentation

Start with the project [README](../README.md). Phase reports (09–15) record what was built and verified in each phase;
the guides (16–20) describe the finished system. `tests/test_docs.py` checks that the documents match the code, the
database schema, the API and the test suite.

| Roadmap deliverable (docs/08, Phase 9) | Document |
|---|---|
| README | [../README.md](../README.md) |
| Architecture | [03_architecture.md](03_architecture.md) (as built) |
| Data sources | [02_data_source_assessment.md](02_data_source_assessment.md) |
| Data dictionary | [05_data_dictionary.md](05_data_dictionary.md) (source fields, cleaning, flags; every column, generated) and [04_database_schema.md](04_database_schema.md) |
| ETL | [09_data_pipeline.md](09_data_pipeline.md) |
| Forecasting methodology | [06_forecasting_methodology.md](06_forecasting_methodology.md) (pre-registered, with dated amendments) |
| Walk-forward report and model comparison | [10_model_evaluation_report.md](10_model_evaluation_report.md); full tables `experiments/forecast/EVALUATION_TABLES.md` |
| MLflow guide | [16_mlflow_guide.md](16_mlflow_guide.md) |
| API docs | [07_api_specification.md](07_api_specification.md), [11_backend_api.md](11_backend_api.md); live: `/docs` on the API |
| Testing report | [17_testing_report.md](17_testing_report.md) |
| Deployment guide | [18_deployment_guide.md](18_deployment_guide.md); reference: [15_infrastructure.md](15_infrastructure.md) |
| Known limitations | [19_known_limitations.md](19_known_limitations.md) |
| Interview guide | [20_interview_guide.md](20_interview_guide.md) |

Other documents: [01 product requirements](01_product_requirements.md), [08 roadmap and status](08_roadmap.md),
[12 MLOps](12_mlops.md), [13 Telegram alerts](13_telegram_alerts.md), [14 dashboard](14_dashboard.md),
[../dashboard/README.md](../dashboard/README.md). Screenshots: `docs/images/` (taken 2026-11-27 from the running
stack with `npm run screenshots`).
