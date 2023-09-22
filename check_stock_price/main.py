import json
import logging
import math
import os
import pathlib
import subprocess
from dataclasses import dataclass
from typing import Optional, Sequence

import boto3
import pendulum
import requests
import typer
from rich import print
from tabulate import SEPARATING_LINE, tabulate
from typing_extensions import Annotated

logger = logging.getLogger('check_stock_price')
handler = logging.FileHandler('check_stock_price.log')
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

app = typer.Typer()


@dataclass
class Parameters:
    target_account_balance: float
    threshold_data_age_minutes: int
    investment_aggression: float
    percentage_fall_threshold: float
    alphavantage_api_key: str


class RoboInvestor:
    def __init__(self, boto_session: boto3.Session, account_balance=10_000.00):
        self.dynamo = boto_session.resource('dynamodb')
        self.table = self.dynamo.Table('stocks')
        self.ssm = boto_session.client('ssm')
        self.parameters = self.load_parameters_from_parameter_store()
        self.tickers = self.load_tickers_from_dynamodb()
        self.account_balance = account_balance

    def load_parameters_from_parameter_store(self):
        response = self.ssm.get_parameters_by_path(Path='/robo-investor/', WithDecryption=True)
        params = {}
        for param in response['Parameters']:
            _prefix, name = param['Name'].rsplit('/', maxsplit=1)
            value = param['Value']
            params[name] = value
            if name != 'alphavantage_api_key':
                logger.info(f'Loaded {name}: {value} from parameter store')
        # I don't know how to get the types from SSM so I'm just casting them here
        return Parameters(
            target_account_balance=float(params['target_account_balance']),
            threshold_data_age_minutes=int(params['threshold_data_age_minutes']),
            investment_aggression=float(params['investment_aggression']),
            percentage_fall_threshold=float(params['percentage_fall_threshold']),
            alphavantage_api_key=str(params['alphavantage_api_key']),
        )

    def save_parameters_to_parameter_store(self, parameters: dict[str, str | int | float]):
        prefix = '/robo-investor/'
        for name, value in parameters.items():
            logger.info(f'Saving {name}: {value} to parameter store')
            self.ssm.put_parameter(Name=prefix + name, Value=value, Type='SecureString', Overwrite=True)

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
        with open(file_path) as f:
            return json.load(f)

    def load_or_request_data(self, ticker, threshold_minutes: Optional[int] = None):
        threshold_minutes = threshold_minutes or self.parameters.threshold_data_age_minutes
        file_path = pathlib.Path(f'data/{ticker}.json')
        if not file_path.exists():
            logger.info(f'{ticker} - No local data, requesting from API')
            data = self.request_api_stock_data(ticker)
            self.save_stock_data_to_local(ticker, data)
        else:
            modified_time = pendulum.from_timestamp(file_path.stat().st_mtime)
            time_diff = pendulum.now().diff(modified_time).minutes
            logger.info(f'{ticker}: data {time_diff} minutes old')
            if time_diff > threshold_minutes:
                logger.info(f'{ticker}: Requesting data from API')
                data = self.request_api_stock_data(ticker)
                self.save_stock_data_to_local(ticker, data)
            else:
                logger.info(f'{ticker}: Loading data from local')
                data = self.load_stock_data_from_local(ticker)
        return data

    def load_tickers_from_dynamodb(self) -> dict[str, float]:
        return {item['ticker']: float(item['threshold']) for item in self.table.scan()['Items']}

    def save_tickers_to_dynamodb(self, tickers: Sequence[tuple[str, float]]) -> None:
        for ticker, price_threshold in tickers:
            self.table.put_item(Item={'ticker': ticker, 'threshold': price_threshold})

    def calculate_investment_dollars(self, account_balance: float, percentage_change: float) -> float:
        difference = account_balance - self.parameters.target_account_balance
        percent_over = difference / self.parameters.target_account_balance
        multiplier = difference * math.exp(percent_over) * self.parameters.investment_aggression
        dollars = multiplier * abs(percentage_change**2)
        return dollars

    def check_stock_prices(self):
        table_data = [
            ('[blue]Checking stock prices...[/blue]', ''),
            SEPARATING_LINE,
            ('Account Balance:', f'${self.account_balance:,.2f}'),
            ('Target Balance:', f'${self.parameters.target_account_balance:,.2f}'),
            ('Aggression (0.0 - 1.0):', f'{self.parameters.investment_aggression:,.2f}'),
            ('Percentage Fall Threshold:', f'{self.parameters.percentage_fall_threshold:,.2f}%'),
            SEPARATING_LINE,
        ]

        for ticker, price_threshold in self.tickers.items():
            data = self.load_or_request_data(ticker)
            latest_data = data['Time Series (5min)']
            latest_timestamp = max(latest_data.keys())
            current_price = float(latest_data[latest_timestamp]['4. close'])
            prev_close_price = float(latest_data[sorted(latest_data.keys())[-2]]['4. close'])
            percentage_change = ((current_price - prev_close_price) / prev_close_price) * 100
            if percentage_change < self.parameters.percentage_fall_threshold:
                purchase_dollars = self.calculate_investment_dollars(self.account_balance, percentage_change)
                shares = int(purchase_dollars // current_price)
                investment_recommendation = f'[green]BUY ${purchase_dollars:,.2f} --> {shares} shares[/green]'
            else:
                investment_recommendation = '[yellow]HOLD[/yellow]'

            stock_info = [
                (f'[green]{ticker}[/green]', ''),
                ('Current Price:', f'${current_price:,.2f}'),
                ('Threshold:', f'${price_threshold:,.2f}'),
                ('Previous Close:', f'${prev_close_price:,.2f}'),
                ('Percentage Change:', f'{percentage_change:,.2f}%'),
                (investment_recommendation, ''),
                SEPARATING_LINE,
            ]
            table_data.extend(stock_info)

        print(tabulate(table_data, tablefmt='plain'))


@app.callback(invoke_without_command=True)
@app.command()
def check(
    ctx: typer.Context,
    account_balance: Annotated[
        Optional[int], typer.Option(show_default=False, help='Set the current account balance')
    ] = None,
    target_balance: Annotated[
        Optional[int], typer.Option(show_default=False, help='Set the target account balance')
    ] = None,
    threshold_minutes: Annotated[
        Optional[int], typer.Option(show_default=False, help='Set threshold for data age in minutes')
    ] = None,
    investment_aggression: Annotated[
        Optional[float], typer.Option(show_default=False, help='Set the investment aggression')
    ] = None,
    percentage_fall_threshold: Annotated[
        Optional[float], typer.Option(show_default=False, help='Set the percentage fall threshold')
    ] = None,
):
    if ctx.invoked_subcommand is not None:
        return

    boto_session = boto3.Session(profile_name='chris.birch.developer@ichrisbirch')
    rbi = RoboInvestor(boto_session)

    if account_balance:
        rbi.account_balance = account_balance

    # Save parameters which will be loaded
    if target_balance:
        logger.info(f'Setting target account balance to {target_balance}')
        rbi.save_parameters_to_parameter_store({'target_account_balance': str(target_balance)})
        rbi.parameters.target_account_balance = target_balance

    if threshold_minutes:
        logger.info(f'Setting threshold data age to {threshold_minutes} minutes')
        rbi.save_parameters_to_parameter_store({'threshold_data_age_minutes': str(threshold_minutes)})
        rbi.parameters.threshold_data_age_minutes = threshold_minutes

    if investment_aggression:
        logger.info(f'Setting investment aggression to {investment_aggression}')
        rbi.save_parameters_to_parameter_store({'investment_aggression': str(investment_aggression)})
        rbi.parameters.investment_aggression = investment_aggression

    if percentage_fall_threshold:
        logger.info(f'Setting percentage fall threshold to {percentage_fall_threshold}')
        rbi.save_parameters_to_parameter_store({'percentage_fall_threshold': str(percentage_fall_threshold)})
        rbi.parameters.percentage_fall_threshold = percentage_fall_threshold

    rbi.check_stock_prices()


@app.command()
def update():
    print('[blue]Updating check-stock-price...[/blue]')
    logger.info('Updating check-stock-price')
    script_location = pathlib.Path().home().joinpath('code/projects/python/robo-investor')
    print(script_location)
    logger.info(f'Script location: {script_location}')
    os.chdir(script_location)

    print('[green]Building new wheel...[/green]')
    logger.info('Building new wheel')
    build_command = 'poetry build --format=wheel'
    logger.info(f'Build command: {build_command}')
    subprocess.call(build_command, shell=True)

    print('[green]Installing new wheel...[/green]')
    logger.info('Installing new wheel')
    wheels = script_location.joinpath('dist').glob('*.whl')
    latest_wheel = sorted(wheels)[-1]
    print(latest_wheel)
    logger.info(f'Latest wheel: {latest_wheel}')
    # pipx upgrade does not work, instead force install
    install_command = f'pipx install --force {latest_wheel}'
    logger.info(f'Install command: {install_command}')
    subprocess.call(install_command, shell=True)
    print('[green]Done![/green]')
    logger.info('Completed update')


if __name__ == "__main__":
    app()
