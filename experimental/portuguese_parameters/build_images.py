"""Offline source-only packaging for the experimental images; never starts services."""
import argparse,hashlib,json,subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
D=['docker','--context','desktop-linux']
BASES={'adapter':('ai-effect-beta-candidate-api:f1e49485e37e','sha256:a294c9a4db5ee39b6a1c24d2d385f996e2f72ec0a0a36c8657ed77139c5ae60d'),
 'wp3':('ai-effect-parameter-wp3:cfc0a850548c','sha256:edcac597f327dc817ff0c9b9c930dbf9b4cb62235d42f2ef1940845ca1820a21')}
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--role',choices=BASES,required=True)
    p.add_argument('--revision',required=True);p.add_argument('--records',type=Path,required=True);a=p.parse_args()
    source=subprocess.check_output(['git','-c','core.longpaths=true','rev-parse',a.revision+'^{commit}'],cwd=ROOT,text=True).strip()
    tag,expected=BASES[a.role]
    assert json.loads(subprocess.check_output(D+['image','inspect',tag]))[0]['Id']==expected,'Retained build-input drift'
    scope=['experimental/portuguese_parameters','orchestrator/src/services/parameter_contract.py','orchestrator/parameter_contract_provenance.json'] if a.role=='adapter' else ['orchestrator/src','experimental/portuguese_parameters/Wp3.Dockerfile']
    archive=subprocess.check_output(['git','-c','core.longpaths=true','archive',source,*scope],cwd=ROOT)
    output=('ai-effect-pt-parameter-adapter:' if a.role=='adapter' else 'ai-effect-pt-wp3:')+source[:12]
    assert subprocess.run(D+['image','inspect',output],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode!=0,'Refuse to overwrite image'
    a.records.mkdir(parents=True,exist_ok=True)
    dockerfile='Dockerfile' if a.role=='adapter' else 'Wp3.Dockerfile'
    with (a.records/(a.role+'-build.log')).open('xb') as log:
        subprocess.run(D+['build','--pull=false','--network=none','--platform','linux/amd64','--build-arg','SOURCE_COMMIT='+source,
            '-f','experimental/portuguese_parameters/'+dockerfile,'-t',output,'-'],input=archive,stdout=log,stderr=subprocess.STDOUT,check=True)
    image=json.loads(subprocess.check_output(D+['image','inspect',output]))[0]
    record={'source':source,'image_id':image['Id'],'tag':output,'context_sha256':hashlib.sha256(archive).hexdigest(),'retained_base':expected}
    (a.records/(a.role+'-image.json')).write_text(json.dumps(record,indent=2));print(json.dumps(record))
if __name__=='__main__':main()
