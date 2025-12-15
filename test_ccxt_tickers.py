#!/usr/bin/env python3
"""
Script para probar el comportamiento de fetch_tickers con diferentes versiones de ccxt
"""
import asyncio
import ccxt.async_support as ccxt
import json

async def test_fetch_tickers():
    """Prueba fetch_tickers y analiza la estructura de datos"""
    print(f"ccxt version: {ccxt.__version__}\n")
    
    # Simular conexión a Binance (sin API keys necesarias para fetch_tickers)
    exchange = ccxt.binanceusdm({
        'enableRateLimit': True,
    })
    
    try:
        print("Fetching markets...")
        markets = await exchange.fetch_markets()
        print(f"Total markets: {len(markets)}\n")
        
        print("Fetching tickers...")
        tickers = await exchange.fetch_tickers()
        print(f"Total tickers: {len(tickers)}\n")
        
        # Analizar tickers con problemas
        problems = []
        sample_tickers = {}
        count_none_last = 0
        count_missing_last = 0
        count_valid = 0
        
        for symbol, ticker in tickers.items():
            if symbol.endswith("USDT") and not symbol.startswith("."):
                if not isinstance(ticker, dict):
                    problems.append(f"{symbol}: ticker is not a dict")
                    continue
                
                if "last" not in ticker:
                    count_missing_last += 1
                    if len(sample_tickers) < 3:
                        sample_tickers[symbol] = {"issue": "missing 'last' key", "ticker": ticker}
                elif ticker["last"] is None:
                    count_none_last += 1
                    if len(sample_tickers) < 3:
                        sample_tickers[symbol] = {"issue": "last is None", "ticker": ticker}
                else:
                    count_valid += 1
                    if len(sample_tickers) < 3 and count_valid <= 3:
                        sample_tickers[symbol] = {"issue": "valid", "ticker": ticker}
        
        print("=" * 60)
        print("ANÁLISIS DE TICKERS:")
        print("=" * 60)
        print(f"Tickers válidos (con 'last' no None): {count_valid}")
        print(f"Tickers con 'last' = None: {count_none_last}")
        print(f"Tickers sin clave 'last': {count_missing_last}")
        print(f"Total problemas: {count_none_last + count_missing_last}")
        print()
        
        print("=" * 60)
        print("MUESTRAS DE TICKERS:")
        print("=" * 60)
        for symbol, info in sample_tickers.items():
            print(f"\n{symbol}:")
            print(f"  Issue: {info['issue']}")
            print(f"  Keys disponibles: {list(info['ticker'].keys())[:10]}")
            if "last" in info['ticker']:
                print(f"  last value: {info['ticker']['last']} (type: {type(info['ticker']['last'])})")
            if "bid" in info['ticker']:
                print(f"  bid value: {info['ticker']['bid']}")
            if "ask" in info['ticker']:
                print(f"  ask value: {info['ticker']['ask']}")
        
        # Mostrar algunos ejemplos de problemas
        if count_none_last > 0 or count_missing_last > 0:
            print("\n" + "=" * 60)
            print("EJEMPLOS DE TICKERS CON PROBLEMAS:")
            print("=" * 60)
            problem_count = 0
            for symbol, ticker in tickers.items():
                if symbol.endswith("USDT") and not symbol.startswith("."):
                    if ("last" not in ticker) or (ticker.get("last") is None):
                        print(f"\n{symbol}:")
                        print(f"  Structure: {json.dumps({k: v for k, v in list(ticker.items())[:5]}, indent=2)}")
                        problem_count += 1
                        if problem_count >= 5:
                            break
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await exchange.close()

if __name__ == "__main__":
    asyncio.run(test_fetch_tickers())

