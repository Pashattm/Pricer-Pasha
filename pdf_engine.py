"""Export du calcul mémorisé : aucun moteur de pricing n'est rappelé."""
from io import BytesIO
from html import escape

def generer_pdf_calcul(snapshot):
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    styles=getSampleStyleSheet(); out=BytesIO(); story=[]
    def texte(t,style='BodyText'): return Paragraph(escape(str(t)),styles[style])
    def nombre(x): return f'{x:,.6g}' if isinstance(x,(int,float)) and not isinstance(x,bool) else str(x)
    def table(rows):
        t=Table([[texte(c) for c in row] for row in rows],colWidths=[230,280],repeatRows=1,hAlign='LEFT')
        t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e9edf2')),('LINEBELOW',(0,0),(-1,-1),.3,colors.lightgrey),('BOTTOMPADDING',(0,0),(-1,-1),6)])); return t
    story += [texte('PRICER - Dernier calcul','Title'),texte(snapshot['route']+' | '+snapshot['date']),Spacer(1,14)]
    story += [texte('Paramètres du calcul','Heading2'),table([['Paramètre','Valeur']]+[[k,nombre(v)] for k,v in snapshot['parametres'].items() if v is not None])]
    r=snapshot['resultats']; story += [Spacer(1,12),texte('Résultats','Heading2'),table([['Indicateur','Valeur']]+[[k,nombre(v)] for k,v in r.items() if not isinstance(v,(dict,list)) and v is not None])]
    for key in ['ic95','ic95_pct']:
        if key in r: story.append(texte(key+' : '+ ' à '.join(nombre(x) for x in r[key])))
    dates=r.get('obs_dates',[]); flows=r.get('flux_attendus_par_date',[]); pv=r.get('flux_attendus_actualises_par_date',[])
    if dates and flows:
        story += [texte('Flux moyens par date','Heading2'),table([['Date (années)','Flux moyen / actualisé']]+[[nombre(t),nombre(flows[i]) + (' / '+nombre(pv[i]) if pv else '')] for i,t in enumerate(dates)])]
    if 'primaires' in r: story += [texte('Grecques','Heading2'),table([['Grecque','Valeur']]+[[k,nombre(v)] for k,v in r['primaires'].items()])]
    if r.get('ST_grid') and r.get('payoff'):
        from reportlab.graphics.shapes import Drawing, PolyLine, String, Line
        xs=r['ST_grid']; ys=r['payoff']; lo,hi=min(ys),max(ys); span=max(hi-lo,1e-9)
        d=Drawing(510,180); d.add(String(15,165,'Remboursement / payoff à maturité (sans rappel antérieur)',fontSize=9))
        points=[]
        for x,y in zip(xs,ys): points += [25+460*(x-min(xs))/max(max(xs)-min(xs),1e-9),25+125*(y-lo)/span]
        d.add(Line(25,25,485,25,strokeColor=colors.grey)); d.add(PolyLine(points,strokeColor=colors.darkorange,strokeWidth=1.4)); d.add(String(25,10,f'Sous-jacent : {min(xs):.3g} à {max(xs):.3g} | payoff : {lo:.3g} à {hi:.3g}',fontSize=8)); story += [Spacer(1,14),d]
    story += [Spacer(1,14),texte('Résultat mémorisé, sans recalcul. Taux et volatilités saisis en %, montants dans la devise des paramètres. Les probabilités de simulation dépendent du modèle et ne sont pas des prévisions. Hors défaut émetteur, frais et funding. Pour les notes, marge affichée = 100 % moins valeur théorique. Pour les accumulateurs, la valeur est celle des P&L actualisés, sans nominal remboursé.')]
    def footer(canvas,doc): canvas.drawRightString(550,25,str(doc.page))
    SimpleDocTemplate(out,pagesize=A4,rightMargin=42,leftMargin=42,topMargin=40,bottomMargin=42).build(story,onFirstPage=footer,onLaterPages=footer)
    return out.getvalue()
