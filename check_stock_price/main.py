import math
import os
import pathlib
import subprocess
import json
import pendulum

import dotenv
import requests
import typer
from rich import print

# This is for the local install and update, possibly not necessary with pipx install
SCRIPT_LOCATION = pathlib.Path().home().joinpath('code/projects/python/check-stock-price')
SAVE_FILE = pathlib.Path('data.json')
TICKERS = [('VOO', 400), ('SCHG', 70), ('SCHX', 50), ('SCHD', 70)]
PERCENTAGE_FALL_THRESHOLD = -0.05
ACCOUNT_BALANCE = 10_000
TARGET_BALANCE = 5_000
AGGRESSION = .5  # 0-1 where 1 is most aggressive

dotenv.load_dotenv(SCRIPT_LOCATION.joinpath('.env'))
API_KEY = os.environ['API_KEY']
app = typer.Typer()


def calculate_investment_dollars(account_balance, target_balance, percentage_change, aggression):
    difference = account_balance - target_balance
    percent_over = difference / target_balance
    multiplier = difference * math.exp(percent_over) * aggression
    dollars = multiplier * abs(percentage_change ** 2)
    return dollars


def save_to_local(ticker, data):
    file_path = pathlib.Path(f'data/{ticker}.json')
    with open(file_path, 'w') as f:
        json.dump(data, f)


def load_from_local(ticker):
    file_path = pathlib.Path(f'data/{ticker}.json')
    with open(file_path, 'r') as f:
        return json.load(f)


def request_api_data(ticker):
    response = requests.get(
        f'https://www.alphavantage.co/query?function=TIME_SERIES_INTRADAY'
        f'&symbol={ticker}&interval=5min&apikey={API_KEY}'
    )
    return response.json()


def load_or_request_data(ticker, threshold_hours=1):
    file_path = pathlib.Path(f'data/{ticker}.json')
    if not file_path.exists():
        data = request_api_data(ticker)
        save_to_local(ticker, data)
    else:
        now = pendulum.now()
        modified = pendulum.from_timestamp(file_path.stat().st_mtime)
        if now.diff(modified).hours > threshold_hours:
            data = request_api_data(ticker)
            save_to_local(ticker, data)
        else:
            data = load_from_local(ticker)
    return data


@app.callback(invoke_without_command=True)
@app.command()
def check(ctx: typer.Context):
    if ctx.invoked_subcommand is not None:
        return

    print('[blue]Checking stock prices...[/blue]')
    print()
    print(f'Account Balance: ${ACCOUNT_BALANCE:,.2f}')
    print(f'Target Balance: ${TARGET_BALANCE:,.2f}')
    print(f'Aggression: Conservative 0 > {AGGRESSION:.2f} > 1 Aggressive')
    print(f'Percentage Fall Threshold: {PERCENTAGE_FALL_THRESHOLD:.2f}%')
    print()

    for ticker, price_threshold in TICKERS:
        data = load_or_request_data(ticker)
        latest_data = data['Time Series (5min)']
        latest_timestamp = max(latest_data.keys())
        current_price = float(latest_data[latest_timestamp]['4. close'])
        prev_close_price = float(latest_data[sorted(latest_data.keys())[-2]]['4. close'])
        percentage_change = ((current_price - prev_close_price) / prev_close_price) * 100
        if percentage_change < PERCENTAGE_FALL_THRESHOLD:
            purchase_dollars = calculate_investment_dollars(
                ACCOUNT_BALANCE, TARGET_BALANCE, percentage_change, AGGRESSION
            )
            shares = purchase_dollars // current_price
            investment_recommendation = f'BUY {purchase_dollars:.2f} --> {shares} shares'
        else:
            investment_recommendation = 'HOLD'

        print(f'[green]{ticker}[/green]')
        print(f'Current Price:\t\t ${current_price:.2f}')
        print(f'Threshold:\t\t ${price_threshold:.2f}')
        print(f'Previous Close:\t\t ${prev_close_price:.2f}')
        print(f'Percentage Change: \t {percentage_change:.2f}%')
        print(f'Investment Recommendation: {investment_recommendation}')
        print()


@app.command()
def update():
    print('[blue]Updating check-stock-price...[/blue]')
    print(SCRIPT_LOCATION)
    os.chdir(SCRIPT_LOCATION)

    print('[green]Building new wheel...[/green]')
    subprocess.call('poetry build', shell=True)

    print('[green]Installing new wheel...[/green]')
    wheel_path = next(SCRIPT_LOCATION.joinpath('dist').glob('*.whl'))
    subprocess.call(f'pip install --quiet --user {wheel_path} --force-reinstall', shell=True)
    print('[green]Done![/green]')


if __name__ == "__main__":
    app()
