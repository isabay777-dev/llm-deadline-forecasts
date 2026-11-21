"""Plot the two within-study intervals from the respective release readers' JSON."""
import argparse
import json
import math
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator


def figure(smol, qwen, out):
    a=smol["deadline_forecasts"]
    b=qwen["confirmation"]
    if b["eligible"]!=b["paired"]:
        raise ValueError("Full-cohort comparison requires complete paired coverage")
    rows=[
        ("SmolLM2-135M", a["active"], a["direct_scale_minus_hazard"], "#275975", "o"),
        ("Qwen2.5-0.5B", b["eligible"], b["direct_minus_hazard"], "#70472d", "s")]
    plt.rcParams.update({"font.family":"DejaVu Sans","font.size":9,"pdf.fonttype":42})
    fig,(ax,ci)=plt.subplots(1,2,figsize=(7.6,1.85),gridspec_kw={"width_ratios":[3.2,1.5]})
    fig.subplots_adjust(left=.212,right=.992,bottom=.32,top=.85,wspace=.03)
    ax.axvspan(-.01,.01,color="#e1e9ec",zorder=0)
    for x in (-.01,.01):
        ax.axvline(x,color="#62757d",lw=.8,ls="--")
    ax.axvline(0,color="#617078",lw=.7)
    ci.set_title("Difference [95% interval]",fontsize=9,loc="center",pad=2)
    span=max(.013,max(max(abs(r[2]["lower_95"]),abs(r[2]["upper_95"])) for r in rows)*1.15)
    for y,(name,n,v,color,marker) in enumerate(rows[::-1]):
        est,lo,hi=(v[k] for k in ("estimate","lower_95","upper_95"))
        if not all(math.isfinite(x) for x in (est,lo,hi)):
            raise ValueError("Unidentified interval")
        ax.errorbar(est,y,xerr=[[est-lo],[hi-est]],fmt=marker,mfc=color,mec=color,color=color,
                    elinewidth=1.9,markersize=5,capsize=4,zorder=3)
        ci.text(.5,y,f"{est:+.4f}  [{lo:+.4f}, {hi:+.4f}]",ha="center",va="center",fontsize=9)
    ax.set_yticks([0,1],[f"{name}\n{n} active" for name,n,*_ in rows[::-1]])
    ax.set_xlabel("Mean Brier difference (direct minus hazard)",fontsize=9)
    ax.xaxis.set_major_locator(MaxNLocator(nbins=5,symmetric=True))
    ax.set_xlim(-span,span)
    ax.set_ylim(-.6,1.55)
    ci.set_xlim(0,1)
    ci.set_ylim(ax.get_ylim())
    ci.axis("off")
    for edge in ("left","right","top"):
        ax.spines[edge].set_visible(False)
    ax.spines["bottom"].set_color("#677278")
    ax.tick_params(axis="y",length=0,pad=8)
    fig.savefig(out,dpi=200,bbox_inches="tight",pad_inches=.07)
    plt.close(fig)


if __name__=="__main__":
    ap=argparse.ArgumentParser(description=__doc__)
    for k in ("smol_results","qwen_results","output"):
        ap.add_argument(k,type=Path)
    args=ap.parse_args()
    figure(json.loads(args.smol_results.read_bytes()),json.loads(args.qwen_results.read_bytes()),args.output)
