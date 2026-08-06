# 実績（PDF）
# 2番: 2000m2 / 掃除45分 / SG1+ブロアー4 = 5人
# 8番: 1500m2 / 掃除25分 / SG1+ブロアー4 = 5人
areas=[2000,1500]; mins=[45,25]; crew=5
tot_a=sum(areas); tot_h=sum(mins)/60
print("掃除 合計面積 %d m2 / 実時間 %.2f h -> 全体能率 %.0f m2/h"%(tot_a,tot_h,tot_a/tot_h))
now_mh_per_1000 = crew*tot_h/tot_a*1000
print("現状 投入人時/1000m2 = %.2f 人時"%now_mh_per_1000)
for a,m in zip(areas,mins):
    print("  %d m2: %.0f m2/h, %.2f 人時/1000m2"%(a,a/(m/60),crew*(m/60)/a*1000))

TOW=8000   # 牽引式ターボブロアー 平坦部処理能力 m2/h（1人=SG運転手）
BP=900     # 背負式 1人あたり m2/h（実績 900〜1000 の下限側）

def after(flat_ratio, bp_men):
    f=flat_ratio
    mh_tow = (1000*f)/TOW*1            # SG運転手1人
    h_bp   = (1000*(1-f))/(BP*bp_men)
    mh_bp  = h_bp*bp_men
    return mh_tow+mh_bp

print()
for name,fr,men in [("A: 平坦70%",0.7,2),("B: 平坦50%",0.5,2),("C: 法面主体 平坦30%",0.3,3)]:
    aft=after(fr,men)
    cut=now_mh_per_1000-aft
    print("%-22s 導入後 %.2f 人時/1000m2  削減 %.2f (%.0f%%)"%(name,aft,cut,cut/now_mh_per_1000*100))

print()
# 年間
DAILY=3500; DAYS=180; ANNUAL=DAILY*DAYS
print("年間掃除面積 %d m2/日 x %d 日 = %s m2"%(DAILY,DAYS,f"{ANNUAL:,}"))
RUN=280000  # 燃料+整備 増分/年
for wage in [1500,2000,2500]:
    print("\n--- 労務単価 %d 円/人時 ---"%wage)
    for name,fr,men in [("A 平坦70%",0.7,2),("B 平坦50%",0.5,2),("C 平坦30%",0.3,3)]:
        cut=now_mh_per_1000-after(fr,men)
        save=cut*ANNUAL/1000*wage
        net=save-RUN
        row="%-10s 削減%6.0f人時/年 節減%8s円 ランニング-%s 純益%9s円"%(
            name, cut*ANNUAL/1000, f"{save:,.0f}", f"{RUN:,}", f"{net:,.0f}")
        pb=[]
        for price in [1500000,3000000,4500000]:
            pb.append("%.1f年"%(price/net) if net>0 else "回収不可")
        print(row+" | 回収 150万:%s 300万:%s 450万:%s"%tuple(pb))
