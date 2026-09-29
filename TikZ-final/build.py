"""Compile all retained current TikZ sources; intermediate files stay outside OneDrive."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse,json,subprocess,shutil,tempfile,sys
ROOT=Path(__file__).resolve().parent
HEADER=r'''\documentclass[border=0pt]{standalone}
\usepackage[UTF8]{ctex}
\usepackage{amsmath}
'''
MATH=r'''\usepackage{unicode-math}
\setmainfont{Times New Roman}
\setmathfont{XITS Math}
'''
TIKZ=r'''\usepackage{tikz}
\usetikzlibrary{arrows.meta,calc,patterns,patterns.meta}
\pagestyle{empty}
\begin{document}
'''
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--jobs',type=int,default=4);parser.add_argument('--files',nargs='*');args=parser.parse_args()
 entries=json.loads((ROOT/'index.json').read_text('utf-8'));entries=[e for e in entries if not args.files or e['file'] in args.files]
 work=Path(tempfile.mkdtemp(prefix='tikz-compile-'));records=[]
 def one(e):
  src=ROOT/e['file'];dest=ROOT/e['pdf'];folder=work/str(e['group'])/src.stem;folder.mkdir(parents=True,exist_ok=True)
  tex=folder/'figure.tex';tex.write_text(HEADER+('' if e['group']=='geometry' else MATH)+TIKZ+'\\input{\\detokenize{'+e['file']+'}}\n\\end{document}\n',encoding='utf-8')
  cmd=['xelatex','-interaction=nonstopmode','-halt-on-error','-file-line-error','-output-directory='+str(folder),str(tex)]
  r=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace')
  record=dict(file=e['file'],command=cmd,cwd=str(ROOT),exit=r.returncode,stdout=r.stdout,stderr=r.stderr)
  if r.returncode==0:
   assert (folder/'figure.pdf').is_file();shutil.copy2(folder/'figure.pdf',dest)
  return record
 with ThreadPoolExecutor(max_workers=args.jobs) as pool:
  futures=[pool.submit(one,e) for e in entries]
  for f in as_completed(futures):
   rec=f.result();records.append(rec)
   if rec['exit']:print('FAIL',rec['file'],rec['stdout'][-1800:],flush=True)
   elif len(records)%20==0 or len(records)==len(entries):print('Compiled',len(records),'/',len(entries),flush=True)
 (work/'verification.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
 print('RECORD',work/'verification.json',flush=True)
 failed=[r for r in records if r['exit']]
 print('RESULT',len(records)-len(failed),'passed;',len(failed),'failed',flush=True)
 return 1 if failed else 0
if __name__=='__main__':sys.exit(main())
