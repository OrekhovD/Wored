f,m,M,T,PX=0.0006,0.0028,8.0,0.5,75540
def prof(name,L,E,tp,sl):
    N=M*L; fees=2*N*f
    dliq0=M/N-m-f; dliq1=(M+E)/N-m-f
    win=N*tp-fees; loss=N*sl+fees
    preq=loss/(loss+win)
    print(f"{name}\t{L}\t{N:.0f}\t{E}\t{tp*100:.3f}\t{sl*100:.3f}\t{dliq0*100:.3f}\t{dliq1*100:.3f}\t{win:.2f}\t{loss:.2f}\t{preq:.3f}\t{tp*PX:.0f}\t{sl*PX:.0f}")
print("profile\tL\tN\tE_topup\ttp_pct\tsl_pct\tdliq_before_pct\tdliq_after_pct\twin_net\tloss_net\tp_required\ttp_usd\tsl_usd")
prof("P0_Conservative",50,0,0.006,0.006)
prof("P1_Base",100,2,0.004,0.006)
prof("P1_Base_sym",100,2,0.006,0.006)
prof("P2_Scalp",150,2,0.0025,0.0025)
prof("P3_Micro200",200,2,0.0015125,0.0012)
