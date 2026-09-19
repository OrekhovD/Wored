"""Strategy math for HTX BTC-USDT isolated, new (V5) risk model.
Liquidation (isolated, new system): MM = N*(mmr + taker) ; liquidate when MM >= margin + uPnL
=> adverse distance d_liq = (M+E)/N - mmr - taker
TP distance for target net profit T with market (taker) entry+exit: d_tp = 2*taker + T/N
"""
from decimal import Decimal as D
PRICE = 75540.0
TAKER, MAKER, MMR = 0.0006, 0.0002, 0.0028
M, T = 8.0, 0.5

def row(L, E=0.0, fee_in=TAKER, fee_out=TAKER):
    N = M * L
    d_tp = fee_in + fee_out + T / N
    d_liq = (M + E) / N - MMR - TAKER
    fees = N * (fee_in + fee_out)
    loss = M + E + N * fee_in  # liquidation: full isolated margin + entry fee
    p_req = loss / (loss + T)
    p_rw = d_liq / (d_liq + d_tp)  # driftless first-passage approximation
    ev_rw = p_rw * T - (1 - p_rw) * loss
    return dict(L=L, E=E, N=N, eff_lev=round(N/(M+E),1), fees=round(fees,3), d_tp=round(d_tp*100,4), tp_usd=round(d_tp*PRICE,1),
                d_liq=round(d_liq*100,4), liq_usd=round(d_liq*PRICE,1), loss=round(loss,2), p_req=round(p_req,4),
                p_rw=round(p_rw,4), ev_rw=round(ev_rw,3))

print("L\tE\tN\teff_lev\tfees_rt\td_tp_pct\ttp_usd\td_liq_pct\tliq_usd\tloss_liq\tp_required\tp_random_walk\tev_random_walk")
for L,E in [(20,0),(50,0),(100,0),(100,2),(125,0),(150,0),(150,2),(160,0),(200,0),(200,2)]:
    r=row(L,E); print("\t".join(str(r[k]) for k in r))
print()
print("maker entry variant")
for L,E in [(100,2),(200,2)]:
    r=row(L,E,fee_in=MAKER); print("\t".join(str(r[k]) for k in r))
# top-up trigger at 50% of initial liq distance
print()
for L in (100,150,200):
    N=M*L; d0=(M)/N-MMR-TAKER; trig=0.5*d0; d1=(M+2)/N-MMR-TAKER
    print(f"L={L} d_liq0={d0*100:.4f}% trigger={trig*100:.4f}% ({trig*PRICE:.0f}$) d_liq_after={d1*100:.4f}% remaining_after_trigger={(d1-trig)*100:.4f}% ({(d1-trig)*PRICE:.0f}$)")
# self-sufficiency: net trades needed to cover LLM cost
for cost in (20,27,45,60):
    print(f"LLM ${cost}/mo -> ${cost/30:.2f}/day -> {cost/30/T:.1f} net-positive $0.5 trades/day above breakeven")
