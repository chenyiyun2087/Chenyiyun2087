# -*- coding: utf-8 -*-
from jqdata import *
from jqfactor import get_factor_values
import datetime
import numpy as np
import pandas as pd


def initialize(context):
    # ========================
    # 1. 基础设置
    # ========================
    set_benchmark('000905.XSHG')
    set_option('use_real_price', True)
    set_option('avoid_future_data', True)
    set_option('order_volume_ratio', 0.25)  # 订单不超过市场成交量的 25%

    # 成本与滑点：按股票口径设置；若你的聚宽环境不支持 PriceRelatedSlippage，可改回 FixedSlippage
    set_slippage(PriceRelatedSlippage(0.002))
    set_order_cost(
        OrderCost(
            open_tax=0,
            close_tax=0.001,
            open_commission=0.0003,
            close_commission=0.0003,
            close_today_commission=0,
            min_commission=5,
        ),
        type='stock'
    )
    log.set_level('order', 'error')

    # ========================
    # 2. 参数区
    # ========================
    g.stock_num = 10                     # 最大持仓数
    g.candidate_num = 15                 # 最终候选池保留数量
    g.limit_days = 20                    # 历史持有列表回看天数（保留原思路）
    g.cooldown_days = 10                 # 卖出后冷却天数
    g.new_stock_days = 375               # 次新过滤天数
    g.min_avg_money = 2e8                # 近20日日均成交额下限，单位：元
    g.min_price = 4.0                    # 最低价格过滤

    # 组合回撤风控阈值
    g.dd_warn = 0.12
    g.dd_reduce = 0.18
    g.dd_stop = 0.22

    # 仓位管理
    g.target_exposure = 1.0
    g.style_exposure = 1.0
    g.risk_state = 'normal'

    # 风格过滤指数
    g.small_cap_index = '000852.XSHG'    # 中证1000
    g.large_cap_index = '000300.XSHG'    # 沪深300

    # 运行时变量
    g.hold_list = []
    g.history_hold_list = []
    g.high_limit_list = []
    g.cooldown_map = {}                  # {stock: last_sell_date}
    g.portfolio_peak = None
    g.current_drawdown = 0.0
    g.last_target_list = []

    # ========================
    # 3. 定时任务
    # ========================
    run_daily(update_risk_state, time='9:05', reference_security='000300.XSHG')
    run_daily(prepare_stock_list, time='9:10', reference_security='000300.XSHG')
    run_weekly(weekly_adjustment, weekday=1, time='9:45', reference_security='000300.XSHG')
    run_daily(check_limit_up, time='14:00', reference_security='000300.XSHG')
    run_daily(print_position_info, time='15:10', reference_security='000300.XSHG')


# ========================
# 风控模块
# ========================
def update_risk_state(context):
    total_value = context.portfolio.total_value
    if g.portfolio_peak is None:
        g.portfolio_peak = total_value
    else:
        g.portfolio_peak = max(g.portfolio_peak, total_value)

    g.current_drawdown = 0 if g.portfolio_peak == 0 else 1 - total_value / g.portfolio_peak

    # 回撤控制仓位
    if g.current_drawdown > g.dd_stop:
        dd_exposure = 0.0
        g.risk_state = 'stop'
    elif g.current_drawdown > g.dd_reduce:
        dd_exposure = 0.3
        g.risk_state = 'reduce_hard'
    elif g.current_drawdown > g.dd_warn:
        dd_exposure = 0.7
        g.risk_state = 'reduce_soft'
    else:
        dd_exposure = 1.0
        g.risk_state = 'normal'

    # 风格过滤：小盘风格明显走弱时主动降仓
    g.style_exposure = get_style_exposure(context)
    g.target_exposure = min(dd_exposure, g.style_exposure)

    log.info('风险状态: %s, 当前回撤: %.2f%%, 风格仓位: %.2f, 目标总仓位: %.2f' % (
        g.risk_state,
        g.current_drawdown * 100,
        g.style_exposure,
        g.target_exposure
    ))


def get_style_exposure(context):
    """
    简单的小盘风格过滤：
    若中证1000跌破20日均线且相对沪深300走弱，则仓位上限降到 50%。
    """
    try:
        df = get_price(
            [g.small_cap_index, g.large_cap_index],
            end_date=context.previous_date,
            frequency='daily',
            fields=['close'],
            count=20,
            panel=False,
            fill_paused=True
        )
        if df is None or len(df) == 0:
            return 1.0
        pivot = df.pivot(index='time', columns='code', values='close').dropna()
        if g.small_cap_index not in pivot.columns or g.large_cap_index not in pivot.columns or len(pivot) < 20:
            return 1.0
        small = pivot[g.small_cap_index]
        large = pivot[g.large_cap_index]
        ratio = small / large
        small_latest = small.iloc[-1]
        small_ma20 = small.mean()
        ratio_latest = ratio.iloc[-1]
        ratio_ma20 = ratio.mean()
        if small_latest < small_ma20 and ratio_latest < ratio_ma20:
            return 0.5
        return 1.0
    except Exception as e:
        log.info('风格过滤异常，默认满仓上限: %s' % str(e))
        return 1.0


# ========================
# 选股模块
# ========================
def get_dividend_ratio_filter_list(context, stock_list, sort, p1, p2):
    time1 = context.previous_date
    time0 = time1 - datetime.timedelta(days=365)
    interval = 1000
    list_len = len(stock_list)
    frames = []

    for start in range(0, list_len, interval):
        sub = stock_list[start:min(list_len, start + interval)]
        q = query(
            finance.STK_XR_XD.code,
            finance.STK_XR_XD.a_registration_date,
            finance.STK_XR_XD.bonus_amount_rmb
        ).filter(
            finance.STK_XR_XD.a_registration_date >= time0,
            finance.STK_XR_XD.a_registration_date <= time1,
            finance.STK_XR_XD.code.in_(sub)
        )
        frames.append(finance.run_query(q))

    if len(frames) == 0:
        return []

    df = pd.concat(frames, axis=0, ignore_index=True) if len(frames) > 1 else frames[0]
    if df is None or len(df) == 0:
        return []

    dividend = df.fillna(0).set_index('code').groupby('code').sum()
    temp_list = list(dividend.index)
    if len(temp_list) == 0:
        return []

    q = query(valuation.code, valuation.market_cap).filter(valuation.code.in_(temp_list))
    cap = get_fundamentals(q, date=time1)
    if cap is None or len(cap) == 0:
        return []
    cap = cap.set_index('code')

    dr = pd.concat([dividend, cap], axis=1, sort=False)
    dr['dividend_ratio'] = (dr['bonus_amount_rmb'] / 10000.0) / dr['market_cap']
    dr = dr.replace([np.inf, -np.inf], np.nan).dropna(subset=['dividend_ratio'])
    if len(dr) == 0:
        return []

    dr = dr.sort_values(by='dividend_ratio', ascending=sort)
    return list(dr.index)[int(p1 * len(dr)):int(p2 * len(dr))]


def get_factor_filter_list(context, stock_list, jqfactor_name, sort, p1, p2):
    yesterday = context.previous_date
    if len(stock_list) == 0:
        return []
    data = get_factor_values(stock_list, jqfactor_name, end_date=yesterday, count=1)
    score_list = data[jqfactor_name].iloc[0].tolist()
    df = pd.DataFrame({'code': stock_list, 'score': score_list}).dropna()
    if len(df) == 0:
        return []
    df = df.sort_values(by='score', ascending=sort)
    return list(df['code'])[int(p1 * len(df)):int(p2 * len(df))]


def filter_liquidity(context, stock_list, days=20, min_avg_money=2e8):
    if len(stock_list) == 0:
        return []
    try:
        df = get_price(
            stock_list,
            end_date=context.previous_date,
            frequency='daily',
            fields=['money', 'close'],
            count=days,
            panel=False,
            fill_paused=False
        )
        if df is None or len(df) == 0:
            return []
        grp = df.groupby('code').agg({'money': 'mean', 'close': 'last'})
        grp = grp[(grp['money'] >= min_avg_money) & (grp['close'] >= g.min_price)]
        return list(grp.index)
    except Exception as e:
        log.info('流动性过滤异常，返回原股票池: %s' % str(e))
        return stock_list


def filter_cooldown_stock(context, stock_list):
    if len(stock_list) == 0:
        return []
    today = context.previous_date
    keep = []
    for stock in stock_list:
        last_sell = g.cooldown_map.get(stock)
        if last_sell is None:
            keep.append(stock)
        else:
            if (today - last_sell).days >= g.cooldown_days:
                keep.append(stock)
    return keep


def get_stock_list(context):
    yesterday = context.previous_date
    initial_list = list(get_all_securities(types=['stock'], date=yesterday).index)
    initial_list = filter_kcbj_stock(initial_list)
    initial_list = filter_new_stock(context, initial_list, g.new_stock_days)
    initial_list = filter_st_stock(initial_list)

    # 1) 高股息（保留前50%）
    dr_list = get_dividend_ratio_filter_list(context, initial_list, False, 0, 0.5)
    # 2) 高换手波动（保留前80%）
    tv_list = get_factor_filter_list(context, dr_list, 'turnover_volatility', False, 0, 0.8)
    # 3) 低杠杆（保留最优50%）
    lev_list = get_factor_filter_list(context, tv_list, 'MLEV', True, 0, 0.5)
    # 4) 流动性过滤
    liq_list = filter_liquidity(context, lev_list, days=20, min_avg_money=g.min_avg_money)
    if len(liq_list) < max(g.stock_num, 10):
        liq_list = lev_list
    # 5) 冷却期过滤
    liq_list = filter_cooldown_stock(context, liq_list)
    if len(liq_list) == 0:
        return []
    # 6) 小流通市值优先
    q = query(valuation.code, valuation.circulating_market_cap).filter(
        valuation.code.in_(liq_list)
    ).order_by(valuation.circulating_market_cap.asc())
    df = get_fundamentals(q, date=yesterday)
    if df is None or len(df) == 0:
        return []
    final_list = list(df['code'])[:g.candidate_num]
    return final_list


# ========================
# 持仓准备与调仓
# ========================
def prepare_stock_list(context):
    g.hold_list = []
    for position in list(context.portfolio.positions.values()):
        stock = position.security
        g.hold_list.append(stock)

    # 保留最近若干天持仓历史（便于复盘）
    g.history_hold_list.append(g.hold_list)
    if len(g.history_hold_list) >= g.limit_days:
        g.history_hold_list = g.history_hold_list[-g.limit_days:]

    # 获取昨日涨停列表
    if len(g.hold_list) > 0:
        df = get_price(
            g.hold_list,
            end_date=context.previous_date,
            frequency='daily',
            fields=['close', 'high_limit'],
            count=1,
            panel=False,
            fill_paused=False
        )
        df = df[df['close'] == df['high_limit']]
        g.high_limit_list = list(df['code'])
    else:
        g.high_limit_list = []

    # 清理冷却期过期股票
    today = context.previous_date
    expired = []
    for stock, last_sell in g.cooldown_map.items():
        if (today - last_sell).days >= g.cooldown_days:
            expired.append(stock)
    for stock in expired:
        g.cooldown_map.pop(stock, None)


def weekly_adjustment(context):
    # 如果触发暂停，直接清仓
    if g.target_exposure <= 0:
        log.info('触发暂停阈值，执行清仓')
        for stock in list(context.portfolio.positions.keys()):
            position = context.portfolio.positions[stock]
            close_position(position, context)
        return

    target_list = get_stock_list(context)
    target_list = filter_paused_stock(target_list)
    target_list = filter_limitup_stock(context, target_list)
    target_list = filter_limitdown_stock(context, target_list)
    target_list = target_list[:min(g.stock_num, len(target_list))]
    g.last_target_list = target_list

    log.info('本周目标池数量: %d, 目标股票: %s' % (len(target_list), str(target_list)))

    # 先卖出不在目标池中的持仓（昨日涨停的股票暂不强行卖出，除非后面 14:00 炸板）
    for stock in list(g.hold_list):
        if stock not in target_list and stock not in g.high_limit_list:
            log.info('卖出[%s]' % stock)
            position = context.portfolio.positions[stock]
            success = close_position(position, context)
            if success:
                g.cooldown_map[stock] = context.current_dt.date()
        else:
            log.info('保留/继续持有[%s]' % stock)

    # 根据总仓位目标进行等权配置
    total_value = context.portfolio.total_value
    if len(target_list) == 0:
        return

    target_total_value = total_value * g.target_exposure
    target_value_each = target_total_value / len(target_list)

    # 再对目标池做等权再平衡
    for stock in target_list:
        order_target_value_(stock, target_value_each)

    # 若当前持仓中有不在目标池但由于涨停未卖掉的，保留到盘中检查


# ========================
# 涨停与交易模块
# ========================
def check_limit_up(context):
    now_time = context.current_dt
    if len(g.high_limit_list) == 0:
        return
    for stock in list(g.high_limit_list):
        if stock not in context.portfolio.positions:
            continue
        current_data = get_price(
            stock,
            end_date=now_time,
            frequency='1m',
            fields=['close', 'high_limit'],
            skip_paused=False,
            fq='pre',
            count=1,
            panel=False,
            fill_paused=True
        )
        if current_data is None or len(current_data) == 0:
            continue
        if current_data.iloc[0, 0] < current_data.iloc[0, 1]:
            log.info('[%s]涨停打开，卖出' % stock)
            position = context.portfolio.positions[stock]
            success = close_position(position, context)
            if success:
                g.cooldown_map[stock] = context.current_dt.date()
        else:
            log.info('[%s]涨停，继续持有' % stock)


def order_target_value_(security, value):
    if value == 0:
        log.debug('Selling out %s' % security)
    else:
        log.debug('Order %s to value %.2f' % (security, value))
    return order_target_value(security, value)


def close_position(position, context):
    security = position.security
    order = order_target_value_(security, 0)
    if order is not None:
        return True
    return False


# ========================
# 通用过滤模块
# ========================
def filter_paused_stock(stock_list):
    current_data = get_current_data()
    return [stock for stock in stock_list if not current_data[stock].paused]


def filter_st_stock(stock_list):
    current_data = get_current_data()
    return [
        stock for stock in stock_list
        if not current_data[stock].is_st
        and 'ST' not in current_data[stock].name
        and '*' not in current_data[stock].name
        and '退' not in current_data[stock].name
    ]


def filter_limitup_stock(context, stock_list):
    if len(stock_list) == 0:
        return []
    last_prices = history(1, unit='1m', field='close', security_list=stock_list)
    current_data = get_current_data()
    result = []
    for stock in stock_list:
        if stock in context.portfolio.positions.keys():
            result.append(stock)
        else:
            if last_prices[stock][-1] < current_data[stock].high_limit:
                result.append(stock)
    return result


def filter_limitdown_stock(context, stock_list):
    if len(stock_list) == 0:
        return []
    last_prices = history(1, unit='1m', field='close', security_list=stock_list)
    current_data = get_current_data()
    result = []
    for stock in stock_list:
        if stock in context.portfolio.positions.keys():
            result.append(stock)
        else:
            if last_prices[stock][-1] > current_data[stock].low_limit:
                result.append(stock)
    return result


def filter_kcbj_stock(stock_list):
    out = []
    for stock in stock_list:
        if stock[0] == '4' or stock[0] == '8' or stock[:2] == '68':
            continue
        out.append(stock)
    return out


def filter_new_stock(context, stock_list, days):
    yesterday = context.previous_date
    return [
        stock for stock in stock_list
        if not (yesterday - get_security_info(stock).start_date < datetime.timedelta(days=days))
    ]


# ========================
# 打印与记录
# ========================
def print_position_info(context):
    trades = get_trades()
    for _trade in trades.values():
        print('成交记录：' + str(_trade))

    print('当前目标仓位比例: %.2f, 当前回撤: %.2f%%, 风险状态: %s' % (
        g.target_exposure, g.current_drawdown * 100, g.risk_state
    ))
    print('本周目标股票池: {}'.format(g.last_target_list))

    for position in list(context.portfolio.positions.values()):
        securities = position.security
        cost = position.avg_cost
        price = position.price
        ret = 100 * (price / cost - 1) if cost > 0 else 0
        value = position.value
        amount = position.total_amount
        print('代码: {}'.format(securities))
        print('成本价: {}'.format(format(cost, '.2f')))
        print('现价: {}'.format(price))
        print('收益率: {}%'.format(format(ret, '.2f')))
        print('持仓(股): {}'.format(amount))
        print('市值: {}'.format(format(value, '.2f')))
        print('———————————————————————————————————')
    print('———————————————————————————————————————分割线————————————————————————————————————————')
