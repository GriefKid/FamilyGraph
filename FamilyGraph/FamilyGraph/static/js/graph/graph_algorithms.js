window.GraphAlgorithms = {

bfsShortestPath(start,end,adj){

let queue=[start]
let visited=new Set([start])
let parent={}

while(queue.length){

let node=queue.shift()

if(node===end) break

adj[node].forEach(n=>{

if(!visited.has(n)){

visited.add(n)
parent[n]=node
queue.push(n)

}

})

}

let path=[]
let cur=end

while(cur){

path.push(cur)
cur=parent[cur]

}

return path.reverse()

},


dfsPath(start,end,adj){

let stack=[start]
let visited=new Set()
let parent={}

while(stack.length){

let node=stack.pop()

if(node===end) break

if(visited.has(node)) continue

visited.add(node)

adj[node].forEach(n=>{

if(!visited.has(n)){

parent[n]=node
stack.push(n)

}

})

}

let path=[]
let cur=end

while(cur){

path.push(cur)
cur=parent[cur]

}

return path.reverse()

},


dijkstra(start,end,adj){

let dist={}
let prev={}
let pq=[]

Object.keys(adj).forEach(n=>{
dist[n]=Infinity
})

dist[start]=0
pq.push({n:start,d:0})

while(pq.length){

pq.sort((a,b)=>a.d-b.d)

let {n}=pq.shift()

if(n===end) break

adj[n].forEach(nei=>{

let alt=dist[n]+1

if(alt<dist[nei]){

dist[nei]=alt
prev[nei]=n
pq.push({n:nei,d:alt})

}

})

}

let path=[]
let cur=end

while(cur){

path.push(cur)
cur=prev[cur]

}

return path.reverse()

},


bidirectional(start,end,adj){

let q1=[start]
let q2=[end]

let p1={}
let p2={}

let v1=new Set([start])
let v2=new Set([end])

while(q1.length && q2.length){

let a=q1.shift()

for(let n of adj[a]){

if(!v1.has(n)){

v1.add(n)
p1[n]=a
q1.push(n)

if(v2.has(n)){
return reconstruct(n,p1,p2)
}

}

}

let b=q2.shift()

for(let n of adj[b]){

if(!v2.has(n)){

v2.add(n)
p2[n]=b
q2.push(n)

if(v1.has(n)){
return reconstruct(n,p1,p2)
}

}

}

}

function reconstruct(mid,p1,p2){

let path1=[]
let cur=mid

while(cur){

path1.push(cur)
cur=p1[cur]

}

path1.reverse()

let path2=[]
cur=p2[mid]

while(cur){

path2.push(cur)
cur=p2[cur]

}

return [...path1,...path2]

}

}

}
