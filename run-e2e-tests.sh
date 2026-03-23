source .env
pytest tests/e2e --alluredir=allure-results
allure serve allure-results
#allure generate --single-file allure-results
#open allure-report/index.html