import pandas as pd


# Load historical price data into a DataFrame
def load_data(file_path):
    data = pd.read_csv(file_path, parse_dates=['Date'])
    return data


# Calculate percentage change over different time periods
def calculate_percentage_change(data, time_period):
    return data['Close'].pct_change(periods=time_period) * 100


# Calculate likelihood and investment percentage
def calculate_likelihood_and_percentage_change(percentage_change):
    # You'll need to implement your own logic here to calculate likelihood and investment percentage
    # based on the percentage change. You might use statistical analysis, machine learning, or any
    # other method that suits your needs.
    # For this example, let's just assume a simple linear relationship for demonstration purposes.
    likelihood = 1 / (1 + abs(percentage_change))
    investment_percentage = min(likelihood * 100, 50)  # Cap investment at 50%

    return likelihood, investment_percentage


# Main function
def main():
    file_path = 'historical_stock_data.csv'
    data = load_data(file_path)

    time_periods = [1, 7, 30, 90, 180, 365, 3 * 365, 5 * 365, 10 * 365]  # Time periods in days

    stock_tickers = data['Ticker'].unique()

    for ticker in stock_tickers:
        stock_data = data[data['Ticker'] == ticker]

        print(f"Stock: {ticker}")
        for period in time_periods:
            percentage_changes = calculate_percentage_change(stock_data, period)
            last_change = percentage_changes.iloc[-1]  # Get the latest percentage change
            likelihood, investment_percentage = calculate_likelihood_and_percentage_change(last_change)

            print(
                f"For {period}-day period: Likelihood: {likelihood:.4f}, Investment Percentage: {investment_percentage:.2f}%"
            )

        print("=" * 40)


if __name__ == "__main__":
    main()
