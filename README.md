# POPCORN v2

Мониторинг криптовалют в реальном времени: watchlist с техническими
сигналами (RSI, MACD, EMA-тренд, Funding Rate, Open Interest) и боковая
панель структурного анализа (поддержка/сопротивление, Fibonacci,
объёмный профиль, risk/reward) по клику на монету.

## Запуск

```bash
pip install -r requirements.txt
streamlit run main.py
```

Откроется на `http://localhost:8501`. Список монет хранится в
`data/coins.json`, создаётся автоматически при первом запуске
(по умолчанию BTCUSDT, ETHUSDT, SOLUSDT).

Приватный API-ключ Binance не требуется — используются только публичные
эндпоинты (klines, funding rate, open interest).

## Структура

```
main.py                  # UI, watchlist, боковая панель анализа
api/get_data.py          # свечи (Binance Spot public API)
api/futures_data.py      # funding rate + open interest (Binance Futures public API)
storage/coins_storage.py # список монет (JSON-файл)
indicators/signal_zones.py  # 5-зонная палитра сигналов
indicators/analysis.py      # поддержка/сопротивление, Fibonacci, объёмный профиль, risk/reward
```

Это не финансовая рекомендация и не готовая торговая система — набор
индикаторов и фильтров для собственного анализа.