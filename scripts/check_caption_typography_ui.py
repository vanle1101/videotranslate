"""Real saved-project typography acceptance, offscreen/muted, no providers.

Only the existing named QA project's style is temporarily edited and restored.
Uses production Qt, HTTP/WebSocket, retained source/WAVs and actual FFmpeg.
"""
from check_preview_style_ui import (
    TASK, launch, close, fingerprint_audio, check_transition_render,
    normalized_style, QApplication, backend, evaluate, until, read_style,
    set_style, event, wait, click, ROOT,
)


def painted(page, seconds=16):
    until(page,"document.getElementById('video-player').readyState>=2",timeout=90)
    evaluate(page,"(() => {const v=document.getElementById('video-player');v.currentTime="+str(seconds)+";v.dispatchEvent(new Event('timeupdate'));return true;})()")
    until(page,"!document.getElementById('video-player').seeking && document.getElementById('subtitle-text').textContent.length>0")
    return evaluate(page,"""(() => {const e=document.getElementById('subtitle-text'),s=getComputedStyle(e);
        const p=document.getElementById('caption-style-panel').getBoundingClientRect();
        const controls=Array.from(document.querySelectorAll('#caption-style-controls input:not([tabindex="-1"]),#caption-style-controls select,#caption-style-controls button'))
          .filter(c=>c.getClientRects().length);
        return {background:s.backgroundColor,font:s.fontFamily,size:parseFloat(s.fontSize),bold:s.fontWeight,
            stroke:s.webkitTextStrokeWidth,strokeColor:s.webkitTextStrokeColor,
            opacity:Number(document.getElementById('caption-background-opacity').value),
            scale:Number(document.getElementById('caption-font-scale-number').value),
            points:document.getElementById('caption-font-size-pt').value === '' ? null : Number(document.getElementById('caption-font-size-pt').value),
            transparent:document.getElementById('caption-transparent-background').checked,
            masks:document.querySelectorAll('.source-subtitle-blur').length,
            controlsFit:controls.every(c=>{const r=c.getBoundingClientRect();return r.left>=p.left&&r.right<=p.right+1}),
            errors:window.__acceptanceErrors};})()""")


def main():
    app=QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window=original=None
    before=fingerprint_audio()
    try:
        window,page,base=launch(app)
        original=read_style(page)
        sample={**original,"background_color":"#334455","text_color":"#ffffff",
            "position":"bottom","blur_original":True,"font_family":"Tahoma",
            "font_scale":100,"font_size_pt":18,"background_opacity":0,"bold":False,"outline":True}
        set_style(page,sample)
        transparent=painted(page)
        assert transparent['background']=='rgba(51, 68, 85, 0)',transparent
        assert 'Tahoma' in transparent['font'] and transparent['bold']=='400',transparent
        assert transparent['size']>0 and float(transparent['stroke'].replace('px',''))>0,transparent
        assert transparent['opacity']==0 and transparent['scale']==100 and transparent['points']==18 and transparent['transparent'],transparent
        assert transparent['masks'] and transparent['controlsFit'] and not transparent['errors'],transparent
        event('TRANSPARENT_TYPOGRAPHY_REAL_UI',transparent)
        snapshot=backend(base,f'/api/streaming/{TASK}')
        assert normalized_style(snapshot['caption_style'])=={k:v.lower() if k.endswith('color') and isinstance(v,str) else v for k,v in sample.items()},snapshot['caption_style']
        check_transition_render(snapshot)
        partial={**sample,'background_opacity':45,'font_size_pt':None,'font_scale':100,'bold':True,'outline':False}
        set_style(page,partial)
        translucent=painted(page)
        assert translucent['background']=='rgba(51, 68, 85, 0.45)',translucent
        assert translucent['size']>0 and translucent['size']!=transparent['size'] and translucent['bold']=='700',translucent
        assert translucent['stroke']=='0px' and not translucent['transparent'],translucent
        event('TRANSLUCENT_BACKGROUND_REAL_UI',translucent)
        for family in ('Arial','Segoe UI','Tahoma','Verdana','Times New Roman'):
            set_style(page,{**partial,'font_family':family})
            state=painted(page)
            assert family in state['font'] and state['controlsFit'],state
        # Exercise the new controls' own handlers, not only a saved snapshot.
        assert click(page,'caption-transparent-background')
        until(page,"document.getElementById('caption-style-status').textContent.startsWith('Đã lưu kiểu phụ đề')")
        toggled=painted(page)
        assert toggled['transparent'] and toggled['opacity']==0 and float(toggled['stroke'].replace('px',''))>0,toggled
        assert backend(base,f'/api/streaming/{TASK}')['caption_style']['background_opacity']==0
        assert click(page,'caption-style-reset')
        until(page,"document.getElementById('caption-style-status').textContent.startsWith('Đã lưu kiểu phụ đề')")
        reset=read_style(page)
        assert reset=={'text_color':'#000000','background_color':None,'position':'auto',
            'blur_original':False,'background_opacity':100,'font_scale':100,'font_size_pt':None,
            'font_family':'Arial','bold':True,'outline':False},reset
        event('REAL_TRANSPARENT_CHECKBOX_AND_RESET',{'toggle':toggled,'reset':reset})
        set_style(page,sample)
        painted(page)
        # Chromium's DOM acknowledgment precedes Qt's compositor frame.
        wait(500)
        window.web_view.grab().save(str(ROOT/'workspace/temp/caption-typography-ui.png'))
        close(app,window)
        window=None
        window,page,base=launch(app)
        assert read_style(page)==sample,read_style(page)
        assert painted(page)['transparent']
        event('TYPOGRAPHY_BACKEND_RESTART_PERSISTED',sample)
        set_style(page,original)
        assert fingerprint_audio()==before
        event('PASS',{'wavs_unchanged':len(before),'style_restored':True,
            'full_video_export_tested':False,'translation_or_tts_calls':0})
        original=None
    finally:
        if window is not None:
            try:
                if original is not None:
                    set_style(window.web_view.page(),original)
            finally:
                close(app,window)
        event('QA_STOPPED',True)


if __name__=='__main__':
    main()
