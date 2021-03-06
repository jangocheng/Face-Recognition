import cv2

import transform
import input

queue = input.generate_input_queue('txt/casia_100.txt')
for i in xrange(100):
    file_path = queue[0][0]
    image, label, landmark = input.read_image_from_disk(queue)
    im, im_rot, im_rez, crop = transform.img_process(image, landmark, print_img=True)
    path = 'images/test_align/' + file_path[-7:-4] 
    cv2.imwrite(path + '_s0_in.jpg', im)
    cv2.imwrite(path + '_s1_rotate.jpg', im_rot)
    cv2.imwrite(path + '_s2_resize.jpg', im_rez)
    cv2.imwrite(path + '_s3_crop.jpg', crop)
    # cv2.imwrite(path + '_out.jpg', ii)

