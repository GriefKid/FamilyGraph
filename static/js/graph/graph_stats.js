window.GraphStats = {

basic(nodes,edges){

return {

nodes:nodes.length,
edges:edges.length,
density:(2*edges.length)/(nodes.length*(nodes.length-1))

}

},


degreeDistribution(nodes,adj){

let degrees=nodes.map(n=>adj[n.id].size)

let max=Math.max(...degrees)
let min=Math.min(...degrees)

let avg=degrees.reduce((a,b)=>a+b,0)/degrees.length

return {min,max,avg}

},


connectedComponents(nodes,adj){

let visited=new Set()
let count=0

nodes.forEach(n=>{

if(!visited.has(n.id)){

count++

let stack=[n.id]

while(stack.length){

let v=stack.pop()

if(visited.has(v)) continue

visited.add(v)

adj[v].forEach(x=>stack.push(x))

}

}

})

return count

},


clusteringCoefficient(nodes,adj){

let total=0

nodes.forEach(n=>{

let neighbors=[...adj[n.id]]

let k=neighbors.length

if(k<2) return

let links=0

for(let i=0;i<k;i++)
for(let j=i+1;j<k;j++)
if(adj[neighbors[i]].has(neighbors[j])) links++

total += (2*links)/(k*(k-1))

})

return total/nodes.length

}

}
