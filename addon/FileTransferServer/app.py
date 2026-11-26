import json, secrets
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

BASE=Path(__file__).resolve().parent; CONFIG_FILE=BASE/'config.json'
def load_config():
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps({'destination':r'C:\FileDrop','port':8765,'username':'transfer','password':'ChangeThisPassword123!','max_file_size':0,'preserve_structure':True,'collision':'rename'},indent=4),encoding='utf-8')
    return json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
config=load_config()
def destination():
    p=Path(config.get('destination',r'C:\FileDrop')); p.mkdir(parents=True,exist_ok=True); return p
app=FastAPI(title='File Transfer Server'); security=HTTPBasic(); templates=Jinja2Templates(directory=str(BASE/'templates'))
app.mount('/static',StaticFiles(directory=str(BASE/'static')),name='static')
def auth(credentials:HTTPBasicCredentials=Depends(security)):
    ok=secrets.compare_digest(credentials.username,str(config.get('username','transfer')))&secrets.compare_digest(credentials.password,str(config.get('password','')))
    if not ok: raise HTTPException(401,'Incorrect username or password',headers={'WWW-Authenticate':'Basic'})
    return credentials.username
@app.get('/')
def home(request:Request,_=Depends(auth)):
    return templates.TemplateResponse(request=request,name='index.html')
@app.get('/api/config')
def get_config(_=Depends(auth)):
    return {'destination':str(destination()),'max_file_size':config.get('max_file_size',0),'preserve_structure':config.get('preserve_structure',True),'collision':config.get('collision','rename')}
@app.post('/api/config')
async def update_config(request:Request,_user=Depends(auth)):
    data=await request.json()
    if 'destination' in data:
        raw=str(data['destination']).strip()
        if not raw: raise HTTPException(400,'Destination path cannot be empty')
        try:
            p=Path(raw).expanduser(); p.mkdir(parents=True,exist_ok=True)
            if not p.is_dir(): raise HTTPException(400,'Destination must be a directory')
            config['destination']=str(p.resolve())
        except HTTPException: raise
        except Exception as e: raise HTTPException(400,f'Cannot use path: {e}')
    if 'max_file_size' in data: config['max_file_size']=max(0,int(data['max_file_size']))
    if 'preserve_structure' in data: config['preserve_structure']=bool(data['preserve_structure'])
    if 'collision' in data:
        if data['collision'] not in ('rename','overwrite','skip'): raise HTTPException(400,'Invalid collision setting')
        config['collision']=data['collision']
    CONFIG_FILE.write_text(json.dumps(config,indent=4,ensure_ascii=False),encoding='utf-8')
    return {'success':True,'config':get_config(_user)}
def safe_target(relative,root):
    rel=str(relative or 'uploaded_file').replace('\\','/').lstrip('/')
    parts=[x for x in rel.split('/') if x not in ('','.','..')]
    target=root.joinpath(*(parts or ['uploaded_file']))
    try: target.resolve().relative_to(root.resolve())
    except ValueError: raise HTTPException(400,'Invalid file path')
    return target
def collision_target(path):
    mode=config.get('collision','rename')
    if not path.exists() or mode in ('overwrite','skip'): return path
    n=1
    while True:
        candidate=path.with_name(f'{path.stem} ({n}){path.suffix}')
        if not candidate.exists(): return candidate
        n+=1
@app.post('/api/upload')
async def upload_files(files:list[UploadFile]=File(...),paths:list[str]=Form(None),_user=Depends(auth)):
    root=destination(); uploaded=[]; skipped=[]; errors=[]; limit=int(config.get('max_file_size',0)); preserve=bool(config.get('preserve_structure',True))
    for i,f in enumerate(files):
        target=None
        try:
            rel=paths[i] if paths and i<len(paths) else (f.filename or 'uploaded_file')
            if not preserve: rel=Path(str(rel).replace('\\','/')).name
            target=safe_target(rel,root); target.parent.mkdir(parents=True,exist_ok=True)
            if config.get('collision')=='skip' and target.exists(): skipped.append(str(rel)); continue
            target=collision_target(target); size=0
            with target.open('wb') as out:
                while True:
                    chunk=await f.read(1024*1024)
                    if not chunk: break
                    size+=len(chunk)
                    if limit and size>limit: raise HTTPException(413,f'File exceeds size limit: {rel}')
                    out.write(chunk)
            uploaded.append({'name':target.name,'path':str(target),'size':size,'time':datetime.now().isoformat(timespec='seconds')})
        except Exception as e:
            if target and target.exists():
                try: target.unlink()
                except OSError: pass
            errors.append({'file':f.filename,'error':e.detail if isinstance(e,HTTPException) else str(e)})
        finally: await f.close()
    return {'success':not errors,'uploaded':uploaded,'skipped':skipped,'errors':errors}
@app.get('/api/history')
def history(_=Depends(auth)):
    try:
        entries=[]
        for p in destination().rglob('*'):
            if p.is_file():
                st=p.stat(); entries.append({'name':p.name,'path':str(p),'size':st.st_size,'modified':datetime.fromtimestamp(st.st_mtime).isoformat(timespec='seconds')})
        entries.sort(key=lambda x:x['modified'],reverse=True); return entries[:100]
    except Exception as e: return JSONResponse(500,content={'error':str(e)})
@app.get('/api/health')
def health(): return {'status':'ok','destination':str(destination()),'time':datetime.now().isoformat(timespec='seconds')}
