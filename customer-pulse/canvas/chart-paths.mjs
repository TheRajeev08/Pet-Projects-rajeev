/** Join observed points only. Gaps get their own path, never a synthetic reading. */
export function chartPaths(values, coordinate, dashed = () => false) {
    const paths = { solid: "", gap: "", dashed: "" };
    let previous = null, lastIndex = -1, lastStyle = null;
    values.forEach((value, index) => {
        if (!Number.isFinite(value)) return;
        const point = coordinate(value, index);
        if (previous !== null) {
            const style = index > lastIndex + 1 ? "gap" : dashed(index) ? "dashed" : "solid";
            paths[style] += (style === lastStyle ? "" : `M${previous}`) + `L${point}`;
            lastStyle = style;
        }
        previous = point;
        lastIndex = index;
    });
    return paths;
}
