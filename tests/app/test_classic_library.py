"""Current Classic record shapes, build provenance and no accidental v83 fallback."""
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PIL import Image
import pytest

from maplebot.app import sprite_library as S
from maplebot.app.classic_library import ClassicWorldLibrary
from maplebot.settings import Profile


@pytest.fixture
def classic(tmp_path, monkeypatch):
    config=json.loads(S.SOURCE_CONFIG.read_text())
    config['provider']='classic-world'
    config['classic_common_items']=['Red Potion']
    config_path=tmp_path/'classic.json'
    config_path.write_text(json.dumps(config))
    monkeypatch.setattr(S,'SOURCE_CONFIG',config_path)
    library=S.SpriteLibrary(tmp_path/'cache')
    assert isinstance(library,ClassicWorldLibrary)
    map_record={'id':'10002061','name':'The Tree That Grew II','mobs':[{'id':'7'},{'id':'7'}],
                'bounds':{'left':-1663,'right':-76},'minimap':{'center_x':1663,'world_scale':16}}
    mob={'id':'7','name':'Slime','hp':115,'stats':{'maxHP':115},'wz':'Mob/0000007.img',
         'animations':[{'name':'move','wz':'Mob/0000007.img/move'},
                       {'name':'die1','wz':'Mob/0000007.img/die1'}]}
    maps={'category':'map','records':[{'id':'10002061','name':'The Tree That Grew II','street_name':'Victoria Road'}]}
    items={'category':'item','records':[{'id':'2000000','name':'Red Potion','icon':{'file':'wz/potion.png'}}]}
    node={'data':{'$preview':{'frames':[{'file':'wz/slime0.png'},{'file':'wz/slime1.png'}]}}}
    library.calls=[]
    library.responses={'map':map_record,'mob':mob,'node':node,'maps':maps,'items':items}
    def fetch(url,limit=40*1024*1024):
        library._check_cancelled()
        library.calls.append(url)
        assert url.startswith('https://maplestory.quest/')
        path=tmp_path/(hashlib.sha256(url.encode()).hexdigest()+'.response')
        parsed=urlsplit(url); query=parse_qs(parsed.query)
        if '/file/' in parsed.path:
            if parsed.path.endswith('index/map.json'): value=library.responses['maps']
            elif parsed.path.endswith('index/item.json'): value=library.responses['items']
            else:
                image=Image.new('RGBA',(12,12),(0,0,0,0))
                color=40 if 'slime0' in url else 90
                for y in range(2,10):
                    for x in range(2,10):image.putpixel((x,y),(color,x*20,y*20,255))
                image.save(path,format='PNG');return path
        elif parsed.path.endswith('/record'): value=library.responses[query['key'][0]]
        elif parsed.path.endswith('/category'):
            value={'items':library.responses['items']['records'],'pages':1}
        else:value=library.responses.get(query.get('node',['node'])[0],library.responses['node'])
        path.write_text(json.dumps(value));return path
    monkeypatch.setattr(library,'_fetch',fetch)
    return library


def test_current_classic_is_default_and_uses_new_map_and_monster_ids(classic,tmp_path):
    found=classic.search_maps('The Tree That Grew II')
    assert found[0]['mapId']=='10002061'
    assert classic.search_maps('101010101')==[]
    manifest=classic.build_pack('10002061',tmp_path/'pack')
    assert {e['id'] for e in manifest['entries'] if e['kind']=='mob'}=={'7'}
    assert manifest['source_game']=='MapleStory Classic World'
    assert manifest['source_client_build']=='1.0.0'
    assert manifest['validated_on_classic'] is False
    assert S.compatible_manifest(manifest)
    assert not any('die1' in url or 'maplestory.io' in url or 'github' in url for url in classic.calls)


def test_current_client_pickup_icons_do_not_claim_server_drop_knowledge(classic,tmp_path):
    manifest=classic.build_pack('10002061',tmp_path/'pack')
    assert any(e['kind']=='item' for e in manifest['entries'])
    assert 'not a server drop table' in manifest['drop_source']
    assert manifest['map_reference']['requires_alignment'] is True
    assert manifest['map_reference']['mob_stats']['7']['hp']==115


def test_current_manifest_loads_and_legacy_bank_does_not(classic,tmp_path):
    pack=tmp_path/'pack';manifest=classic.build_pack('10002061',pack)
    profile=Profile(tmp_path/'profile');profile.ensure()
    S.install_pack(pack,profile.templates_dir)
    assert len(profile.mob_templates())==2
    record=profile.templates_dir/'sprite-library.json'
    installed=json.loads(record.read_text())
    installed.pop('source_game')
    installed['catalog_repository']='andrenogrib/gms_v83_wztoweb'
    record.write_text(json.dumps(installed))
    assert profile.mob_templates()==[]
    assert S.compatible_manifest(installed) is False


def test_unknown_source_build_is_not_silently_loaded(classic,tmp_path):
    manifest=classic.build_pack('10002061',tmp_path/'pack')
    manifest['source_client_build']='0.9.0'
    assert not S.compatible_manifest(manifest)


def test_truncated_or_layered_animations_are_reported_not_replaced_with_v83(classic,tmp_path):
    classic.responses['node']['data']['$preview']['truncated']=True
    with pytest.raises(S.AssetError,match='No Classic sprites'):
        classic.build_pack('10002061',tmp_path/'pack')
    assert all('maplestory.quest' in url for url in classic.calls)


def test_raw_file_paths_are_validated_before_network(classic):
    with pytest.raises(S.AssetError):classic._file('../private')
    with pytest.raises(S.AssetError):classic._file('https://evil.com/image.png')


def test_source_game_id_mismatch_is_rejected(classic,tmp_path):
    classic.responses['mob']['id']='210100'
    with pytest.raises(S.AssetError,match='different asset ID'):
        classic.build_pack('10002061',tmp_path/'pack')


def test_source_caption_identifies_current_game_and_community_origin(classic):
    text=S.source_caption()
    assert 'Classic World 1.0.0' in text
    assert 'Community' in text
    assert 'GMS v83' not in text


def test_per_tick_reference_budget_does_not_start_with_only_one_species():
    from maplebot.perception.sprites import interleave_references
    paths=[Path(f'mob_library_{mid}_move_{frame}.png') for mid in (6,7,11) for frame in range(7)]
    ordered=interleave_references(paths)
    assert {p.name.split('_')[2] for p in ordered[:3]}=={'6','7','11'}
    assert len(ordered)==len(paths) and set(ordered)==set(paths)


def test_real_shape_map_references_are_downloaded_checked_and_installed(classic,tmp_path):
    classic.responses['map'].update(wz='Map/Map/Map0/010002061.img',
        minimap={'width':1600,'height':1600,'center_x':0,'center_y':0,'world_scale':16,
                 'image':{'file':'wz/map.png'}},mobs=[{'id':'7','x':400,'y':780,'cy':800,'fh':1}])
    classic.responses['foothold']={'data':{'$type':'Property','children':{
        '0':{'$type':'Property','children':{'1':{'$type':'Property','children':{
            '1':{'$type':'Property','children':{'x1':'100','x2':'1500','y1':'800','y2':'800'}}}}}}}}}
    classic.responses['ladderRope']={'data':{'$type':'Property','children':{}}}
    pack=tmp_path/'map-pack';manifest=classic.build_pack('10002061',pack)
    assert len(manifest['reference_files'])==1
    geometry=manifest['map_reference']['geometry']
    assert len(geometry['platforms'])==1 and len(geometry['spawn_zones'])==1
    profile=Profile(tmp_path/'map-profile');profile.ensure()
    installed=S.install_pack(pack,profile.templates_dir)
    from maplebot.app.map_zones import reference_descriptor
    desc=reference_descriptor(profile,installed)
    settings=profile.load();settings.calibration.map_reference={k:v for k,v in desc.items() if k!='path'}
    assert Path(profile.map_reference_descriptor(settings)['path']).is_file()
