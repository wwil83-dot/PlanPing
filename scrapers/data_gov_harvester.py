name: PlanPing scraper

on:
  schedule:
    - cron: '0 3 * * *'
    - cron: '0 4 * * *'
    - cron: '0 9 * * *'
  workflow_dispatch:
    inputs:
      job:
        description: 'Which job to run'
        required: true
        default: 'scrape'
        type: choice
        options: [scrape, geocode, alerts]

jobs:
  scrape:
    if: >
      (github.event_name == 'workflow_dispatch' && github.event.inputs.job == 'scrape') ||
      (github.event_name == 'schedule' && github.event.schedule == '0 3 * * *')
    runs-on: ubuntu-latest
    timeout-minutes: 40
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install asyncpg httpx beautifulsoup4 lxml
      - name: Run harvester
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
        run: cd scrapers && python data_gov_harvester.py

  geocode:
    if: >
      (github.event_name == 'workflow_dispatch' && github.event.inputs.job == 'geocode') ||
      (github.event_name == 'schedule' && github.event.schedule == '0 4 * * *')
    runs-on: ubuntu-latest
    timeout-minutes: 40
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install asyncpg httpx
      - name: Geocode missing coordinates
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
        run: cd scrapers && python geocoder.py

  alerts:
    if: >
      (github.event_name == 'workflow_dispatch' && github.event.inputs.job == 'alerts') ||
      (github.event_name == 'schedule' && github.event.schedule == '0 9 * * *')
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install asyncpg httpx
      - name: Dispatch alerts
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
          RESEND_API_KEY: ${{ secrets.RESEND_API_KEY }}
          FROM_EMAIL: ${{ secrets.FROM_EMAIL }}
          BASE_URL: ${{ secrets.BASE_URL }}
        run: python dispatch_alerts.py
