"""JSON adapters with reproducible response snapshots and explicit offline behavior."""
import hashlib
import json
import subprocess
from datetime import datetime,timezone
from urllib.parse import urlencode

class Client:
    def __init__(self,cache,refresh=False):
        self.cache=cache;cache.mkdir(parents=True,exist_ok=True)
        self.refresh=refresh;self.events=[]

    def get_json(self,url,params=None):
        url=url+('?' + urlencode(params) if params else '')
        key=hashlib.sha256(url.encode()).hexdigest()
        path=self.cache/(key+'.json')
        if path.exists() and not self.refresh:
            record=json.loads(path.read_text())
            self.events.append(dict(url=url,status='cached',retrieved_at=record['retrieved_at'],sha256=record['sha256']))
            return record['response'],url
        if not self.refresh:
            raise RuntimeError('No response snapshot; run with --refresh: '+url)
        try:
            proc=subprocess.run(['curl','--fail','--location','--silent','--show-error','--max-time','45',
                '--user-agent','HeatOS-infrastructure-research/1.0',url],capture_output=True,check=True)
            data=json.loads(proc.stdout)
            if isinstance(data,dict) and ('error' in data or data.get('remark')):
                raise ValueError(str(data.get('error') or data.get('remark')))
            record=dict(url=url,retrieved_at=datetime.now(timezone.utc).isoformat(),
                        sha256=hashlib.sha256(proc.stdout).hexdigest(),response=data)
            path.write_text(json.dumps(record,indent=2))
            self.events.append({k:v for k,v in record.items() if k!='response'}|{'status':'downloaded'})
            return data,url
        except Exception as e:
            error=e.stderr.decode(errors='replace') if isinstance(e,subprocess.CalledProcessError) else str(e)
            self.events.append(dict(url=url,status='failed',error=error))
            raise RuntimeError(error) from e
