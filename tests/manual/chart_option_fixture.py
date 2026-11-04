"""Synthetic adapters for the real option service/router; never load OAuth wiring."""
import copy
import sys
import time
import types
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

from chart_watchlist_fixture import SyntheticQuoteManager

# chart_options imports these defaults even when all dependencies are injected.
# Install explicit forbidden defaults before that import to exclude auth/env/DB.
client = types.ModuleType('app.upstox_client')
async def forbidden_remote(*args, **kwargs):
    raise RuntimeError('Remote market/auth access forbidden in synthetic fixture')
client._authenticated_get = forbidden_remote
client.fetch_option_chain = forbidden_remote
client._base_url = lambda: 'https://synthetic.invalid'
sys.modules['app.upstox_client'] = client

from app.chart_data import IST
from app.chart_options import ChartOptionsService
from app.routers import chart_options as router

NOTICE = 'SYNTHETIC OPTION TEST — no broker; generated quotes only.'
SOURCE = 'synthetic_ui_fixture'

class FixtureOptionsService(ChartOptionsService):
    async def expiries(self, key):
        value = await super().expiries(key)
        value.update(source=SOURCE, warnings=[NOTICE])
        return value

    async def snapshot(self, *args, **kwargs):
        value = await super().snapshot(*args, **kwargs)
        value['source'] = SOURCE
        value['warnings'] = [NOTICE, *value.get('warnings', [])]
        for row in value['rows']:
            for leg in (row['ce'], row['pe']):
                if leg:
                    leg.update(quote_source=SOURCE, iv_source=SOURCE)
        return value

class FixtureOptionMarket:
    def __init__(self, underlyings):
        self.underlyings = {v['instrument_key']: v for v in underlyings}
        today = datetime.now(IST).date()
        self.expiries = [(today + timedelta(days=n)).isoformat() for n in (3, 10)]
        self.contracts, self.instruments, self.values, self.overrides = {}, {}, {}, {}
        for index, underlying in enumerate(underlyings):
            key, symbol, exchange = underlying['instrument_key'], underlying['symbol'], underlying['exchange']
            spot, step = (25000, 50) if symbol == 'NIFTY' else (75000, 100)
            rows = []
            for date in self.expiries:
                for offset in range(-45, 46):
                    strike = spot + offset * step
                    for side in ('CE', 'PE'):
                        option_key = f'{exchange}_FO|SYNTH_{index}_{date}_{strike}_{side}'
                        name = f'SYNTHETIC {symbol} {date} {strike} {side}'
                        contract = dict(instrument_key=option_key, trading_symbol=name, name=NOTICE,
                            underlying_key=key, underlying_symbol=symbol, expiry=date,
                            strike_price=strike, instrument_type=side, lot_size=25, exchange_token=option_key.split('|')[1])
                        rows.append(contract)
                        self.instruments[option_key] = dict(instrument_key=option_key, symbol=name, name=NOTICE,
                            exchange=exchange, segment=f'{exchange}_FO', kind='option', volume_available=True,
                            expiry=date, strike=strike, option_type=side, underlying_key=key,
                            underlying=symbol, lot_size=25, exchange_token=contract['exchange_token'])
                        # ATM CE demonstrates literal zero; adjacent CE demonstrates missing metrics.
                        zero, missing = offset == 0 and side == 'CE', offset == 1 and side == 'CE'
                        self.values[option_key] = dict(ltp=0 if zero else 100 + abs(offset),
                            oi=None if missing else 0 if zero else 10000 + abs(offset),
                            prev_oi=None if missing else 0 if zero else 9900,
                            volume=None if missing else 0 if zero else 500,
                            iv=None if missing else 0 if zero else 12.345)
            self.contracts[key] = rows

    async def resolve(self, key):
        value = self.underlyings.get(key) or self.instruments.get(key)
        if not value:
            raise ValueError('Synthetic option chain unavailable for this instrument')
        return copy.deepcopy(value)

    async def get(self, url):
        key = parse_qs(urlparse(url).query)['instrument_key'][0]
        return {'status': 'success', 'data': copy.deepcopy(self.contracts.get(key, []))}

    def metrics(self, key):
        value = dict(self.values[key])
        # Preserve zero/null demonstrations; other legs change each second.
        if value['ltp']:
            pulse = int(time.time()) % 10
            value['ltp'] += pulse / 10
            if value['oi'] is not None: value['oi'] += pulse
            if value['volume'] is not None: value['volume'] += pulse
        return {**value, **self.overrides.get(key, {})}

    async def chain(self, key, expiry):
        underlying = self.underlyings[key]
        out = {}
        for item in self.contracts[key]:
            if item['expiry'] != expiry: continue
            strike = item['strike_price']
            row = out.setdefault(strike, dict(underlying_key=key, expiry=expiry, strike_price=strike,
                underlying_spot_price=25000 if underlying['symbol'] == 'NIFTY' else 75000))
            values = self.metrics(item['instrument_key'])
            row['call_options' if item['instrument_type'] == 'CE' else 'put_options'] = dict(
                instrument_key=item['instrument_key'], market_data={k: v for k, v in values.items() if k != 'iv'},
                option_greeks={'iv': values['iv']})
        return {'status': 'success', 'data': list(out.values())}

class OptionQuoteManager(SyntheticQuoteManager):
    def __init__(self, instruments, price, option_market):
        super().__init__(instruments, price)
        self.option_market = option_market

    def latest_tick_map(self):
        ticks = super().latest_tick_map()
        for key in self.option_market.instruments:
            values = self.option_market.metrics(key)
            ticks[key].update(last_price=values['ltp'], open_interest=values['oi'], volume_traded_today=values['volume'])
        return ticks

def configure(app, option_market):
    app.state.option_market = option_market
    app.state.options_service = FixtureOptionsService(catalog=option_market, get=option_market.get, chain=option_market.chain)
    router.get_chart_options_service = lambda: app.state.options_service
    router.get_quote_manager = lambda: app.state.quote_manager
    app.include_router(router.api, prefix='/api/charts')
