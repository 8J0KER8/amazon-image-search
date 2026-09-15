(() => {
    const MAX_DIMENSION = 1600;
    const COMPRESSION_THRESHOLD = 1024 * 1024;

    function imageFileName(file) {
        const baseName = (file.name || "image").replace(/\.[^.]+$/, "");
        return `${baseName}.jpg`;
    }

    function loadImage(file) {
        return new Promise((resolve, reject) => {
            const imageUrl = URL.createObjectURL(file);
            const image = new Image();

            image.onload = () => {
                URL.revokeObjectURL(imageUrl);
                resolve(image);
            };

            image.onerror = () => {
                URL.revokeObjectURL(imageUrl);
                reject(new Error("Unable to read image"));
            };

            image.src = imageUrl;
        });
    }

    window.optimizeImageForUpload = async file => {
        if (
            !file
            || !file.type.startsWith("image/")
            || file.size <= COMPRESSION_THRESHOLD
        ) {
            return file;
        }

        try {
            const image = await loadImage(file);
            const scale = Math.min(
                1,
                MAX_DIMENSION / Math.max(image.naturalWidth, image.naturalHeight)
            );
            const width = Math.max(1, Math.round(image.naturalWidth * scale));
            const height = Math.max(1, Math.round(image.naturalHeight * scale));
            const canvas = document.createElement("canvas");
            const context = canvas.getContext("2d", { alpha: false });

            canvas.width = width;
            canvas.height = height;
            context.fillStyle = "#ffffff";
            context.fillRect(0, 0, width, height);
            context.drawImage(image, 0, 0, width, height);

            const blob = await new Promise(resolve => {
                canvas.toBlob(resolve, "image/jpeg", 0.86);
            });

            if (!blob || blob.size >= file.size) {
                return file;
            }

            return new File(
                [blob],
                imageFileName(file),
                {
                    type: "image/jpeg",
                    lastModified: file.lastModified
                }
            );

        } catch (error) {
            return file;
        }
    };
})();
