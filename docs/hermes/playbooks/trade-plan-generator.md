# Trade Plan Generator Playbook

## Purpose
The Trade Plan Generator creates advisory trading plans by combining multiple analytical sources into a single coherent strategy recommendation.

## Components

### Input Parameters
- `--symbol`: Trading pair (e.g. BTCUSDT)
- `--period`: Timeframe (e.g. 60min, 4hour)
- `--lookback-days`: Historical data range for analysis
- `--balance`: Account balance for risk calculation
- `--risk-pct`: Percentage of balance at risk
- `--format`: Output format (json, markdown, telegram)

### Data Sources Used
1. `market_context.py` - Market overview and technical levels
2. `signal_explainer.py` - Signal interpretation
3. `pattern_lab.py` - Pattern recognition
4. `risk_position.py` - Risk management calculations
5. `forecast_reality_analyzer.py` - Forecast consensus

### Bias Calculation
The system scores market conditions using multiple factors:
- Moving average relationships (SMA20 > SMA50 = +15 points)
- MACD direction (+15 for bullish)
- RSI levels (+10 for 45-65, -10 for >72 or <30)
- Volume patterns (+10 for positive continuation)
- Forecast consensus (+10 for alignment)

Based on total score:
- ≥65: Bullish
- 45-64: Moderately Bullish
- 35-44: Neutral
- 20-34: Moderately Bearish
- <20: Bearish

### Output Structure
The generator produces:
- Entry zone with price range
- Stop loss level
- Two take profit targets
- Risk metrics (position size, max loss)
- Supporting reasons
- Warning conditions
- Invalidations

## Usage Examples

### Command Line
```bash
# JSON output for programmatic use
python scripts/trade_plan_generator.py \
  --symbol BTCUSDT \
  --period 60min \
  --lookback-days 7 \
  --balance 1000 \
  --risk-pct 1 \
  --format json

# Markdown output for documentation
python scripts/trade_plan_generator.py \
  --symbol BTCUSDT \
  --period 60min \
  --lookback-days 7 \
  --balance 1000 \
  --risk-pct 1 \
  --format markdown

# Telegram output for notifications
python scripts/trade_plan_generator.py \
  --symbol BTCUSDT \
  --period 60min \
  --lookback-days 7 \
  --balance 1000 \
  --risk-pct 1 \
  --format telegram
```

## Integration Points

### With Hermes Commander Mode
This script serves as the foundation for the "Собери план по BTCUSDT на 60min" command workflow.

### Data Quality Handling
- If data sources are unavailable, the system continues with available data
- Missing sources are reported in the output
- If insufficient data exists, returns "insufficient_data" status

## Testing
```bash
# Test all output formats
python scripts/trade_plan_generator.py --symbol BTCUSDT --period 60min --lookback-days 7 --balance 1000 --risk-pct 1 --format json
python scripts/trade_plan_generator.py --symbol BTCUSDT --period 60min --lookback-days 7 --balance 1000 --risk-pct 1 --format markdown
python scripts/trade_plan_generator.py --symbol BTCUSDT --period 60min --lookback-days 7 --balance 1000 --risk-pct 1 --format telegram
```

## Security Considerations
- No secrets are printed or stored
- Does not execute trades, only advisory
- Does not modify runtime configuration
- Works in minimal Python environment