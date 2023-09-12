import json
import math
import os
import pathlib
import subprocess
from dataclasses import dataclass

import boto3
import pendulum
import requests
import typer
from rich import print


# This is for the local install and update, possibly not necessary with pipx install
SCRIPT_LOCATION = pathlib.Path().home().joinpath('code/projects/python/check-stock-price')
TICKERS = [('VOO', 400), ('SCHG', 70), ('SCHX', 50), ('SCHD', 70)]
PERCENTAGE_FALL_THRESHOLD = -0.05
ACCOUNT_BALANCE = 10_000
TARGET_BALANCE = 5_000
AGGRESSION = 0.5  # 0-1 where 1 is most aggressive

# dotenv.load_dotenv(SCRIPT_LOCATION.joinpath('.env'))
# API_KEY = os.environ['API_KEY']
app = typer.Typer()


@dataclass
class Parameters:
    target_account_balance: float
    threshold_data_age_hours: int
    investment_aggression: float
    percentage_fall_threshold: float
    alphavantage_api_key: str


class RoboInvestor:
    def __init__(self, boto_session):
        self.dynamo = boto_session.resource('dynamodb')
        self.table = self.dynamo.Table('stocks')
        self.ssm = boto_session.client('ssm')
        self.parameters = Parameters(**self.load_parameters_from_parameter_store())
        self.tickers = self.load_tickers_from_dynamodb()

    def load_parameters_from_parameter_store(self):
        response = self.ssm.get_parameters_by_path(Path='/robo-investor/', WithDecryption=True)
        params = {}
        for param in response['Parameters']:
            params[param['Name'].split('/')[-1]] = param['Value']
        # I don't know how to get the types from SSM so I'm just casting them here
        params['target_account_balance'] = float(params['target_account_balance'])
        params['threshold_data_age_hours'] = int(params['threshold_data_age_hours'])
        params['investment_aggression'] = float(params['investment_aggression'])
        params['percentage_fall_threshold'] = float(params['percentage_fall_threshold'])
        return params

    def save_parameters_to_parameter_store(self, parameters):
        for name, value in parameters.items():
            self.ssm.put_parameter(Name=name, Value=value, Type='SecureString', Overwrite=True)

    def request_api_stock_data(self, ticker):
        return requests.get(
            f'https://www.alphavantage.co/query?function=TIME_SERIES_INTRADAY'
            f'&symbol={ticker}&interval=5min&apikey={self.parameters.alphavantage_api_key}'
        ).json()

    def save_stock_data_to_local(self, ticker, data):
        file_path = pathlib.Path(f'data/{ticker}.json')
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w') as f:
            json.dump(data, f)

    def load_stock_data_from_local(self, ticker):
        file_path = pathlib.Path(f'data/{ticker}.json')
        with open(file_path, 'r') as f:
            return json.load(f)

    def load_or_request_data(self, ticker, threshold_hours=1):
        file_path = pathlib.Path(f'data/{ticker}.json')
        if not file_path.exists():
            data = self.request_api_stock_data(ticker)
            self.save_stock_data_to_local(ticker, data)
        else:
            modified_time = pendulum.from_timestamp(file_path.stat().st_mtime)
            if pendulum.now().diff(modified_time).hours > threshold_hours:
                data = self.request_api_stock_data(ticker)
                self.save_stock_data_to_local(ticker, data)
            else:
                data = self.load_stock_data_from_local(ticker)
        return data

    def load_tickers_from_dynamodb(self):
        return {item['ticker']: item['threshold'] for item in self.table.scan()['Items']}

    def save_tickers_to_dynamodb(self, tickers):
        for ticker, price_threshold in tickers:
            self.table.put_item(Item={'ticker': ticker, 'threshold': price_threshold})

    def calculate_investment_dollars(self, account_balance, percentage_change):
        difference = account_balance - self.parameters.target_account_balance
        percent_over = difference / self.parameters.target_account_balance
        multiplier = difference * math.exp(percent_over) * self.parameters.investment_aggression
        dollars = multiplier * abs(percentage_change**2)
        return dollars

    def check_stock_prices(self):
        print('[blue]Checking stock prices...[/blue]')
        print()
        print(f'Account Balance: ${ACCOUNT_BALANCE:,.2f}')
        print(f'Target Balance: ${self.parameters.target_account_balance:,.2f}')
        print(f'Aggression: Conservative 0 > {self.parameters.investment_aggression:.2f} > 1 Aggressive')
        print(f'Percentage Fall Threshold: {self.parameters.percentage_fall_threshold:.2f}%')
        print()

        for ticker, price_threshold in self.tickers.items():
            data = self.load_or_request_data(ticker)
            latest_data = data['Time Series (5min)']
            latest_timestamp = max(latest_data.keys())
            current_price = float(latest_data[latest_timestamp]['4. close'])
            prev_close_price = float(latest_data[sorted(latest_data.keys())[-2]]['4. close'])
            percentage_change = ((current_price - prev_close_price) / prev_close_price) * 100
            if percentage_change < self.parameters.percentage_fall_threshold:
                purchase_dollars = self.calculate_investment_dollars(ACCOUNT_BALANCE, percentage_change)
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


@app.callback(invoke_without_command=True)
@app.command()
def check(ctx: typer.Context):
    if ctx.invoked_subcommand is not None:
        return

    boto_session = boto3.Session(profile_name='chris.birch.developer@ichrisbirch')
    rbi = RoboInvestor(boto_session)
    rbi.check_stock_prices()


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
