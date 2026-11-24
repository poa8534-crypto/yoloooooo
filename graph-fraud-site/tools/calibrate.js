// Dev tool (not needed by the site): re-runs the paper's Experiment 0 on the in-browser engine.
// Usage:  node tools/calibrate.js
const GE = require(require('path').join(__dirname, '../js/engine.js'));
const seeds = 30; const acc = {}; const names=['D1','D2','D3','D4','D5','union','vote2'];
let t0=Date.now(), E=0, md=0;
for (let s=0;s<seeds;s++){
  const g = GE.generate({N:1000,f:0.05,seed:s}); E+=g.m;
  const truth = GE.truthSet(g); const r = GE.runAll(g);
  for (const n of names){ const fl = n==='union'?r.union:n==='vote2'?r.vote2:r[n].flag; const m = GE.prf(fl,truth);
    acc[n]=acc[n]||{p:0,r:0,f1:0,fl:0}; acc[n].p+=m.p; acc[n].r+=m.r; acc[n].f1+=m.f1; acc[n].fl+=m.flagged; }
  if(s===0){ const st=GE.degreeStats(g); console.log('seed0: n',g.n,'edges',g.m,'mu',st.mu.toFixed(2),'median',st.median,'max',st.max,'sigma',st.sigma.toFixed(2),'A',g.meta.A,'out/in',g.meta.nOut,g.meta.nIn,'rings',g.meta.rings.length,'clus',g.meta.clusters.length,'truth',truth.size,
   'auroc D1',GE.auroc(r.D1.score,truth).toFixed(3));
   console.log('recall by type D2',GE.recallByType(g,r.D2.flag).map(x=>x&&x.toFixed(2)),'D4',GE.recallByType(g,r.D4.flag).map(x=>x&&x.toFixed(2)),'D5',GE.recallByType(g,r.D5.flag).map(x=>x&&x.toFixed(2))); }
}
console.log('avg edges',E/seeds,' time ms/inst',(Date.now()-t0)/seeds);
console.log('paper: D1 .643/.187/.285 D2 .696/.466/.557 D3 .347/.195/.246 D4 .915/.470/.619 D5 .453/.192/.266 U .505/.938/.655 V2 .867/.391/.534');
for (const n of names) console.log(n, (acc[n].p/seeds).toFixed(3),(acc[n].r/seeds).toFixed(3),(acc[n].f1/seeds).toFixed(3),'flagged',(acc[n].fl/seeds).toFixed(1));
